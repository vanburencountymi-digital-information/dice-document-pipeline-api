"""Generate /Alt text for <Figure> elements in a tagged PDF via Claude Vision.

Walks the PDF structure tree, finds every <Figure> element without alt text,
renders the page region via PyMuPDF, and calls Claude claude-sonnet-4-6 Vision.
Writes /Alt strings back to the struct tree in-place.

Returns a list of API call records for cost tracking (DIC-561).
"""

from __future__ import annotations

import base64
from collections import defaultdict
from pathlib import Path
from typing import Any

# Images smaller than this are treated as decorative (skip Claude call).
_MIN_PX = 60 * 60


def generate_alt_text(
    tagged_path: Path,
    *,
    document_title: str = "",
) -> list[dict[str, Any]]:
    """Write /Alt text to every undecorated <Figure> element in tagged_path.

    Opens the file twice: once read-only to collect figures + extract images
    via PyMuPDF, once writable to write the alt strings back via pikepdf.
    Returns a list of API call records.
    """
    try:
        import fitz  # PyMuPDF
        import pikepdf
    except ImportError as exc:
        raise RuntimeError("PyMuPDF and pikepdf are required for alt text generation") from exc

    from dice_document_pipeline.extraction.client import make_client

    # ── 1. Collect <Figure> elements needing alt text ─────────────────────────
    figures_by_page: dict[int, list[dict]] = defaultdict(list)

    with pikepdf.open(tagged_path) as pdf:
        if "/StructTreeRoot" not in pdf.Root:
            return []
        page_objid_to_idx = _build_page_map(pdf)
        _collect_figures(pdf, pdf.Root["/StructTreeRoot"], page_objid_to_idx, figures_by_page)

    if not figures_by_page:
        return []

    # ── 2. For each page, extract images and call Claude Vision ───────────────
    client = make_client()
    doc = fitz.open(str(tagged_path))
    # (page_idx, fig_n) → alt text string
    alt_results: dict[tuple[int, int], str] = {}
    api_calls: list[dict[str, Any]] = []

    for page_idx in sorted(figures_by_page):
        page_figs = figures_by_page[page_idx]
        page = doc[page_idx]
        images = page.get_images(full=True)  # [(xref, smask, w, h, bpc, cs, ...), ...]

        for fig_n, _ in enumerate(page_figs):
            if fig_n < len(images):
                xref, _, w, h = images[fig_n][:4]
                if w * h < _MIN_PX:
                    alt_results[(page_idx, fig_n)] = ""  # decorative — leave blank
                    continue
                try:
                    img_info = doc.extract_image(xref)
                    img_bytes = img_info["image"]
                    ext = img_info["ext"]
                    media_type = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
                except Exception:
                    img_bytes, media_type = _render_page(page)
            else:
                # No matching image XObject — render whole page
                img_bytes, media_type = _render_page(page)

            alt = _call_claude(client, img_bytes, media_type, document_title, page_idx + 1)
            alt_results[(page_idx, fig_n)] = alt
            api_calls.append({
                "provider": "anthropic",
                "operation": "alt_text",
                "model": "claude-sonnet-4-6",
                "page": page_idx + 1,
                "figure_n": fig_n,
                "alt_text_chars": len(alt),
            })

    doc.close()

    if not alt_results:
        return api_calls

    # ── 3. Write /Alt back to the struct tree ─────────────────────────────────
    figures_by_page2: dict[int, list[dict]] = defaultdict(list)
    with pikepdf.open(tagged_path, allow_overwriting_input=True) as pdf:
        if "/StructTreeRoot" not in pdf.Root:
            return api_calls
        page_objid_to_idx2 = _build_page_map(pdf)
        _collect_figures(pdf, pdf.Root["/StructTreeRoot"], page_objid_to_idx2, figures_by_page2)

        for page_idx, page_figs in figures_by_page2.items():
            for fig_n, fig_info in enumerate(page_figs):
                alt = alt_results.get((page_idx, fig_n), "")
                if alt:
                    fig_info["elem"]["/Alt"] = pikepdf.String(alt)

        pdf.save()

    return api_calls


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_page_map(pdf) -> dict[int, int]:
    """Map each page's object ID → its 0-based index."""
    import pikepdf
    m: dict[int, int] = {}
    for i, page in enumerate(pdf.pages):
        try:
            obj = pdf.get_object(page.objgen) if not isinstance(page, pikepdf.Dictionary) else page
            m[obj.objgen[0]] = i
        except Exception:
            pass
    return m


def _collect_figures(
    pdf,
    node,
    page_map: dict[int, int],
    out: dict[int, list],
    *,
    depth: int = 0,
    inherited_page=None,
) -> None:
    """Recursively collect <Figure> elements that lack /Alt."""
    import pikepdf

    if depth > 30 or not isinstance(node, pikepdf.Dictionary):
        return

    pg_ref = node.get("/Pg", inherited_page)
    page_idx: int | None = None
    if pg_ref is not None:
        try:
            obj = pdf.get_object(pg_ref.objgen) if not isinstance(pg_ref, pikepdf.Dictionary) else pg_ref
            page_idx = page_map.get(obj.objgen[0])
        except Exception:
            pass

    s_type = str(node.get("/S", ""))
    if s_type == "/Figure":
        alt = node.get("/Alt")
        if (alt is None or str(alt).strip() == "") and page_idx is not None:
            out[page_idx].append({"elem": node, "page_idx": page_idx})

    kids = node.get("/K")
    if kids is None:
        return
    items = list(kids) if isinstance(kids, pikepdf.Array) else [kids]
    for kid in items:
        try:
            child = kid if isinstance(kid, pikepdf.Dictionary) else pdf.get_object(kid.objgen)
            _collect_figures(pdf, child, page_map, out, depth=depth + 1, inherited_page=pg_ref)
        except Exception:
            pass


def _render_page(page, dpi: int = 120) -> tuple[bytes, str]:
    """Render a PyMuPDF page to PNG bytes."""
    import fitz
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    return pix.tobytes("png"), "image/png"


def _call_claude(
    client,
    img_bytes: bytes,
    media_type: str,
    document_title: str,
    page_num: int,
) -> str:
    """Call Claude Vision and return a concise alt text string."""
    context = f'This is page {page_num} of a Van Buren County government document titled "{document_title}".' if document_title else f"This is page {page_num} of a Van Buren County government document."

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=200,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": base64.standard_b64encode(img_bytes).decode(),
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            f"{context}\n\n"
                            "Write a concise alt text description for this image suitable for a screen reader. "
                            "Keep it under 125 characters. "
                            "If the image is purely decorative (a rule, border, or background), respond with exactly: Decorative image"
                        ),
                    },
                ],
            }
        ],
    )
    return response.content[0].text.strip()
