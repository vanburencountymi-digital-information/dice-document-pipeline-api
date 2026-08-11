import os
import re
from collections import defaultdict

import pikepdf
import pymupdf as fitz

from remediation.adapters.base import AltTextAdapter, FigureCandidate, ImageMediaType

MAX_STRUCT_TREE_DEPTH = 30

# Images smaller than this (in px^2) are treated as decorative — no Claude call.
MIN_DECORATIVE_PX = 60 * 60

# OpenDataLoader stamps `/Alt = "image " + counter` on every untagged `<Figure>` it
# produces (AutoTaggingProcessor.java's `setStringEntry`/`IMAGE_REPLACEMENT_TEXT`) — a
# PDF/UA-compliance placeholder, not a real description (its own source comment calls
# it "a known false alternative for AT users"). Without this exclusion, every figure
# reaching this stage would already look "described" and get silently skipped.
_PLACEHOLDER_ALT_PATTERN = re.compile(r"^image \d+$")

# PyMuPDF's `extract_image()` "ext" -> the media type Claude Vision actually accepts.
# An embedded format outside this set (e.g. bmp, tiff) falls back to a full-page render
# rather than mislabeling it.
_SUPPORTED_MEDIA_TYPES: dict[str, ImageMediaType] = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
}


class PikePdfAdapter(AltTextAdapter):
    """Wraps pikepdf + PyMuPDF to collect and write back alt text for `<Figure>` struct
    elements (ADR 0003 stage 6, "Enrich figures"). Deliberately never uses a figure's
    derived bounding box to crop anything — see the docling bug-list memory for why that's
    unreliable. Instead: the *n*-th untagged `<Figure>` on a page is ordinally paired with
    the *n*-th image XObject PyMuPDF reports for that page, and the raw embedded image is
    extracted directly; if no XObject lines up, the whole page is rendered instead. Ported
    from v1's `alt_text.py`, not redesigned — see ADR 0005.
    """

    @property
    def name(self) -> str:
        return "pikepdf Adapter"

    def collect_figures(self, pdf_path: str) -> list[FigureCandidate]:
        """Returns one `FigureCandidate` per `<Figure>` struct element missing `/Alt`, in
        struct-tree traversal order per page — the same order `write_alt_text` re-derives,
        so `FigureCandidate.ref` stays valid across the two separate `pikepdf.open()` calls.
        """
        try:
            with pikepdf.open(pdf_path) as pdf:
                figures_by_page = self._walk_figures(pdf)
        except Exception as exc:
            self.raise_adapter_error(f"pikepdf failed to collect figures: {exc}")

        if not figures_by_page:
            return []

        candidates: list[FigureCandidate] = []
        try:
            with fitz.open(pdf_path) as doc:
                for page_idx in sorted(figures_by_page):
                    page_figs = figures_by_page[page_idx]
                    page = doc[page_idx]
                    images = page.get_images(full=True)

                    for fig_n in range(len(page_figs)):
                        image_bytes, media_type, decorative = self._extract_candidate_image(
                            doc, page, images, fig_n
                        )
                        candidates.append(
                            FigureCandidate(
                                ref=(page_idx, fig_n),
                                page_number=page_idx + 1,
                                image_bytes=image_bytes,
                                media_type=media_type,
                                decorative=decorative,
                            )
                        )
        except Exception as exc:
            self.raise_adapter_error(f"PyMuPDF failed to extract figure images: {exc}")

        return candidates

    def write_alt_text(
        self, pdf_path: str, *, output_dir: str, alt_by_ref: dict[tuple[int, int], str]
    ) -> str:
        """Writes `alt_by_ref`'s strings back to their matching `<Figure>` elements (re-
        walked fresh, same as `collect_figures`) and saves into `output_dir` under the same
        filename. Same as v1: a falsy alt (decorative, empty string) leaves `/Alt` unset
        rather than writing an empty string.
        """
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, os.path.basename(pdf_path))

        try:
            with pikepdf.open(pdf_path) as pdf:
                figures_by_page = self._walk_figures(pdf)
                for page_idx, page_figs in figures_by_page.items():
                    for fig_n, elem in enumerate(page_figs):
                        alt = alt_by_ref.get((page_idx, fig_n))
                        if alt:
                            elem["/Alt"] = pikepdf.String(alt)
                pdf.save(output_path)
        except Exception as exc:
            self.raise_adapter_error(f"pikepdf failed to write alt text: {exc}")

        return output_path

    def _extract_candidate_image(
        self, doc: fitz.Document, page: fitz.Page, images: list, fig_n: int
    ) -> tuple[bytes, ImageMediaType, bool]:
        if fig_n >= len(images):
            image_bytes, media_type = self._render_page(page)
            return image_bytes, media_type, False

        xref, _, w, h = images[fig_n][:4]
        if w * h < MIN_DECORATIVE_PX:
            return b"", "image/png", True

        try:
            img_info = doc.extract_image(xref)
            resolved_media_type = _SUPPORTED_MEDIA_TYPES.get(img_info["ext"])
            if resolved_media_type is None:
                raise ValueError(f"unsupported embedded image format: {img_info['ext']}")
            return img_info["image"], resolved_media_type, False
        except Exception:
            image_bytes, media_type = self._render_page(page)
            return image_bytes, media_type, False

    def _render_page(self, page: fitz.Page, dpi: int = 120) -> tuple[bytes, ImageMediaType]:
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        return pix.tobytes("png"), "image/png"

    def _walk_figures(self, pdf: pikepdf.Pdf) -> dict[int, list[pikepdf.Object]]:
        figures_by_page: dict[int, list[pikepdf.Object]] = defaultdict(list)
        if "/StructTreeRoot" not in pdf.Root:
            return figures_by_page

        page_map = self._build_page_map(pdf)
        self._collect_figure_elements(pdf, pdf.Root.StructTreeRoot, page_map, figures_by_page)
        return figures_by_page

    def _build_page_map(self, pdf: pikepdf.Pdf) -> dict[int, int]:
        return {page.obj.objgen[0]: i for i, page in enumerate(pdf.pages)}

    def _collect_figure_elements(
        self,
        pdf: pikepdf.Pdf,
        node: pikepdf.Object,
        page_map: dict[int, int],
        out: dict[int, list[pikepdf.Object]],
        *,
        depth: int = 0,
        inherited_page: pikepdf.Object | None = None,
    ) -> None:
        if depth > MAX_STRUCT_TREE_DEPTH or not isinstance(node, pikepdf.Dictionary):
            return

        pg_ref = node.get("/Pg", inherited_page)
        page_idx = None
        if isinstance(pg_ref, pikepdf.Dictionary):
            page_idx = page_map.get(pg_ref.objgen[0])

        if str(node.get("/S", "")) == "/Figure":
            alt_text = str(node.get("/Alt", "")).strip()
            needs_alt = not alt_text or bool(_PLACEHOLDER_ALT_PATTERN.fullmatch(alt_text))
            if needs_alt and page_idx is not None:
                out[page_idx].append(node)

        kids = node.get("/K")
        if kids is None:
            return
        items = list(kids) if isinstance(kids, pikepdf.Array) else [kids]
        for kid in items:
            if isinstance(kid, pikepdf.Dictionary):
                self._collect_figure_elements(
                    pdf, kid, page_map, out, depth=depth + 1, inherited_page=pg_ref
                )
