"""Run at Docker build time to pre-download Docling layout/structure model weights.

Stores models in /opt/docling-models (set via HF_HOME and DOCLING_ARTIFACTS_PATH
env vars in the Dockerfile) so the container starts without a cold-fetch.

OCR is handled by Tesseract (system package) — no OCR model weights to download.
"""
import os

os.makedirs("/opt/docling-models", exist_ok=True)

from docling.datamodel.pipeline_options import PdfPipelineOptions, TesseractCliOcrOptions
from docling.document_converter import DocumentConverter, PdfFormatOption

pipeline_options = PdfPipelineOptions()
pipeline_options.do_ocr = True
pipeline_options.ocr_options = TesseractCliOcrOptions()

DocumentConverter(
    format_options={"pdf": PdfFormatOption(pipeline_options=pipeline_options)}
)

print("Docling layout/structure models baked in successfully (Tesseract OCR, no extra weights needed).")
