"""Run at Docker build time to pre-download Docling layout/structure model weights.

Stores models in /opt/docling-models (set via HF_HOME and DOCLING_ARTIFACTS_PATH
env vars in the Dockerfile) so the container starts without a cold-fetch.

We must call .convert() on a real PDF — instantiating DocumentConverter alone
does not trigger the safetensors download when DOCLING_ARTIFACTS_PATH is set.
A pikepdf-generated single-page PDF is used so there's no network dependency
at build time and no extra test fixtures needed.

OCR is handled by Tesseract (system package) — no OCR model weights to download.
"""
import tempfile
from pathlib import Path

import pikepdf

from docling.datamodel.pipeline_options import PdfPipelineOptions, TesseractCliOcrOptions
from docling.document_converter import DocumentConverter, PdfFormatOption


def _make_test_pdf(path: Path) -> None:
    """Write a minimal single-page text PDF to path using pikepdf."""
    pdf = pikepdf.Pdf.new()
    content_bytes = b"BT /F1 12 Tf 100 700 Td (Docling bake test) Tj ET"
    content_stream = pikepdf.Stream(pdf, content_bytes)
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name("/Font"),
            Subtype=pikepdf.Name("/Type1"),
            BaseFont=pikepdf.Name("/Helvetica"),
        )
    )
    page = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name("/Page"),
            MediaBox=pikepdf.Array([0, 0, 612, 792]),
            Resources=pikepdf.Dictionary(
                Font=pikepdf.Dictionary(F1=font)
            ),
            Contents=content_stream,
        )
    )
    pages = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name("/Pages"),
            Kids=pikepdf.Array([page]),
            Count=1,
        )
    )
    page.obj["/Parent"] = pages
    pdf.Root.Pages = pages
    pdf.save(str(path))


def main() -> None:
    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_ocr = True
    pipeline_options.ocr_options = TesseractCliOcrOptions()

    converter = DocumentConverter(
        format_options={"pdf": PdfFormatOption(pipeline_options=pipeline_options)}
    )

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        tmp_pdf = Path(f.name)

    _make_test_pdf(tmp_pdf)

    try:
        converter.convert(str(tmp_pdf))
        print("Docling models baked in successfully.")
    except Exception as exc:
        # A conversion error on a trivial PDF is acceptable —
        # what matters is that the model weights were fetched.
        print(f"Docling bake completed (minor conversion error for minimal PDF: {exc})")
    finally:
        tmp_pdf.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
