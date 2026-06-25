"""Run at Docker build time to pre-download Docling model weights.

Stores models in /opt/docling-models (set via HF_HOME and
DOCLING_ARTIFACTS_PATH env vars in the Dockerfile) so Cloud Run
containers start without fetching ~3 GB over the network on first request.
"""
import os

os.makedirs("/opt/docling-models", exist_ok=True)

from docling.document_converter import DocumentConverter  # noqa: E402

DocumentConverter()
print("Docling models baked in successfully.")
