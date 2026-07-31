"""Run at Docker build time to pre-download Docling's model weights.

Stores models under DOCLING_ARTIFACTS_PATH/HF_HOME (set in
Dockerfile.opendataloader-hybrid) so a fresh container never fetches them
over the network on first request. This matters for Cloud Run's scale-to-zero cold starts.

For EasyOCR engine,`force_full_page_ocr=True` is needed to force the OCR path
to actually run against the tiny test PDF below and pull EasyOCR's
recognition models too, not just the layout/table-structure models that
download regardless of OCR settings.

We must call .convert(), not just instantiate DocumentConverter() —
instantiation alone does not trigger the safetensors download once
DOCLING_ARTIFACTS_PATH is set.
"""

import tempfile
from pathlib import Path

import pikepdf
from docling.datamodel.pipeline_options import EasyOcrOptions, PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption


def _make_test_pdf(path: Path) -> None:
    """Write a minimal single-page text PDF to path using pikepdf.

    Avoids a network fetch or checked-in fixture just to have something to
    convert at build time.
    """
    pdf = pikepdf.Pdf.new()
    content_stream = pikepdf.Stream(pdf, b"BT /F1 12 Tf 100 700 Td (Docling bake test) Tj ET")
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
            Resources=pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font)),
            Contents=content_stream,
        )
    )
    pages = pdf.make_indirect(
        pikepdf.Dictionary(Type=pikepdf.Name("/Pages"), Kids=pikepdf.Array([page]), Count=1)
    )
    page["/Parent"] = pages
    pdf.Root.Pages = pages
    pdf.save(str(path))


def main() -> None:
    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_ocr = True
    pipeline_options.ocr_options = EasyOcrOptions(force_full_page_ocr=True, download_enabled=True)

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
        # A conversion error on a trivial PDF is acceptable — what matters
        # is that the model weights were fetched.
        print(f"Docling bake completed (minor conversion error for minimal PDF: {exc})")
    finally:
        tmp_pdf.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
