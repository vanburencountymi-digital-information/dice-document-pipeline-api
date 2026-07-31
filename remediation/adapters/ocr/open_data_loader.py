import glob
import os

import opendataloader_pdf

from remediation.adapters.base import OCRAdapter


class OpenDataLoaderAdapter(OCRAdapter):
    """Wraps OpenDataLoader's Python API to OCR and auto-tag a PDF in one pass (ADR 0004).

    Pinned as a real, named dependency rather than hidden behind a generic OCR/tagging
    interface — OpenDataLoader's tagging pass always runs its own internal extraction/OCR
    first, so this one call backs the whole `ocr` step; there's no separate OCR call to make.
    Always runs in `--hybrid docling-fast` mode per ADR 0004 — not configurable, since the
    ADR didn't decide to support a non-hybrid fallback. That backend lives at `hybrid_url`,
    a separate container in docker-compose (`Dockerfile.opendataloader-hybrid`) since it
    needs Docling/PyTorch, which don't ship musl/Alpine wheels like the rest of this app's
    image.
    """

    HYBRID_MODE = "docling-fast"

    def __init__(self, hybrid_url: str | None = None) -> None:
        self.hybrid_url = hybrid_url

    @property
    def name(self) -> str:
        return "OpenDataLoader Adapter"

    def tag(self, pdf_path: str, *, output_dir: str) -> str:
        """Auto-tags `pdf_path`, writing the tagged PDF into `output_dir`.

        Returns the tagged PDF's path. OpenDataLoader's Python API doesn't document its
        output filename convention or what it raises on failure (missing Java, a bad PDF,
        a hybrid server that's unreachable or still downloading its models, etc.), so this
        reads the result back off disk rather than trusting a return value, and wraps any
        underlying exception rather than naming specific ones.
        """
        os.makedirs(output_dir, exist_ok=True)
        kwargs = {"hybrid_url": self.hybrid_url} if self.hybrid_url else {}
        try:
            opendataloader_pdf.convert(
                input_path=[pdf_path],
                output_dir=output_dir,
                format="tagged-pdf",
                hybrid=self.HYBRID_MODE,
                **kwargs,
            )
        except Exception as exc:
            self.raise_adapter_error(f"opendataloader-pdf failed: {exc}")

        stem = os.path.splitext(os.path.basename(pdf_path))[0]
        matches = glob.glob(os.path.join(output_dir, f"{stem}*.pdf"))
        if not matches:
            self.raise_adapter_error(f"opendataloader-pdf produced no tagged PDF in {output_dir}")
        if len(matches) > 1:
            self.raise_adapter_error(
                f"opendataloader-pdf produced multiple candidate outputs in {output_dir}: {matches}"
            )
        return matches[0]
