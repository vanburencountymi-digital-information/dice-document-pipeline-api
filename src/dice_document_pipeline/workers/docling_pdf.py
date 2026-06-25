"""Docling PDF conversion adapter for the DICE document pipeline.

Mirrors the adobe_pdf_services.py adapter boundary. Docling imports are lazy
so the package loads without Docling installed on machines that don't need it.

Initialize DoclingPdfAdapter once and reuse across documents — model loading
(~1-2 GB, first run only) is expensive; subsequent calls are fast.

Pipeline options lifted from eval/docling/test_docling.py:
  images_scale=1.0 (half of Docling's 2.0 default) and no page/picture image
  generation — we care about text and structure, not rendered images.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class DoclingConversionResult:
    """Structured output from a Docling PDF conversion."""

    html_content: str
    markdown_content: str
    pages: int | None
    tables: int | None
    figures: int | None
    headings: int | None
    word_count: int | None
    api_calls: list[dict[str, Any]] = field(default_factory=list)


class DoclingAdapter(Protocol):
    """Interface for Docling PDF-to-HTML/markdown conversion."""

    def convert_pdf(self, source_pdf: Path) -> DoclingConversionResult:
        """Convert a PDF to HTML and markdown via Docling."""


class DoclingPdfAdapter:
    """Docling-backed PDF conversion adapter.

    Initialise once at worker startup; the converter holds models in memory
    and is reused for every document in the same process lifetime.
    """

    def __init__(
        self,
        *,
        images_scale: float = 1.0,
        generate_page_images: bool = False,
        generate_picture_images: bool = False,
        do_ocr: bool = True,
    ):
        self._images_scale = images_scale
        self._generate_page_images = generate_page_images
        self._generate_picture_images = generate_picture_images
        self._do_ocr = do_ocr
        self._converter = self._build_converter()

    def _build_converter(self):
        try:
            from docling.datamodel.pipeline_options import PdfPipelineOptions
            from docling.document_converter import DocumentConverter, PdfFormatOption

            pipeline_options = PdfPipelineOptions()
            pipeline_options.images_scale = self._images_scale
            pipeline_options.generate_page_images = self._generate_page_images
            pipeline_options.generate_picture_images = self._generate_picture_images
            # Always enable OCR so image-only (scanned) PDFs produce usable text.
            # Docling uses EasyOCR on GPU when available, Tesseract as fallback.
            pipeline_options.do_ocr = self._do_ocr
            return DocumentConverter(
                format_options={"pdf": PdfFormatOption(pipeline_options=pipeline_options)}
            )
        except (ImportError, TypeError):
            # Fallback if the installed Docling version has a different API shape.
            from docling.document_converter import DocumentConverter

            return DocumentConverter()

    def convert_pdf(self, source_pdf: Path) -> DoclingConversionResult:
        start = time.time()
        result = self._converter.convert(str(source_pdf))
        elapsed = round(time.time() - start, 3)

        doc = result.document
        html_content = ""
        markdown_content = ""
        try:
            html_content = doc.export_to_html()
        except Exception:
            pass
        try:
            markdown_content = doc.export_to_markdown()
        except Exception:
            pass

        metrics = _extract_metrics(doc, markdown_content)
        api_call = {
            "provider": "docling",
            "operation": "convert_pdf",
            "mode": "local",
            "elapsed_seconds": elapsed,
            "source_pdf": source_pdf.name,
            **metrics,
        }
        return DoclingConversionResult(
            html_content=html_content,
            markdown_content=markdown_content,
            pages=metrics.get("pages"),
            tables=metrics.get("tables"),
            figures=metrics.get("figures"),
            headings=metrics.get("headings"),
            word_count=metrics.get("word_count"),
            api_calls=[api_call],
        )


class LocalDoclingStub:
    """No-op Docling adapter for local tests and dry runs.

    Returns minimal placeholder HTML/markdown so the rest of the pipeline
    can exercise storage upload, result assembly, and status paths without
    needing Docling installed or a real PDF to convert.
    """

    def convert_pdf(self, source_pdf: Path) -> DoclingConversionResult:
        return DoclingConversionResult(
            html_content=f"<!-- docling local_stub: {source_pdf.name} -->",
            markdown_content=f"<!-- docling local_stub: {source_pdf.name} -->",
            pages=None,
            tables=None,
            figures=None,
            headings=None,
            word_count=None,
            api_calls=[
                {
                    "provider": "docling",
                    "operation": "convert_pdf",
                    "mode": "local_stub",
                    "transaction_count": 0,
                }
            ],
        )


def _extract_metrics(doc, markdown_content: str) -> dict[str, Any]:
    """Pull structural metrics from a DoclingDocument."""
    metrics: dict[str, Any] = {}
    try:
        metrics["pages"] = len(doc.pages)
    except Exception:
        metrics["pages"] = None
    try:
        metrics["tables"] = len(list(doc.tables))
    except Exception:
        metrics["tables"] = None
    try:
        metrics["figures"] = len(list(doc.pictures))
    except Exception:
        metrics["figures"] = None
    try:
        metrics["headings"] = sum(
            1 for line in markdown_content.splitlines() if line.startswith("#")
        )
        metrics["word_count"] = len(markdown_content.split())
    except Exception:
        metrics["headings"] = None
        metrics["word_count"] = None
    return metrics
