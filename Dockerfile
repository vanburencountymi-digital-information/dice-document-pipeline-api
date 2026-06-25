# DICE Document Pipeline — Cloud Run GPU image
#
# Base: CUDA 12.3 + cuDNN 9 runtime on Ubuntu 22.04.
# Docling transformer models are baked in during build to avoid cold-start
# downloads on Cloud Run. Poppler, Tesseract, and Ghostscript are installed
# for the ADA remediation pass.
#
# Build:
#   docker build -t dice-document-pipeline .
#
# Run locally (no GPU, stub adapters):
#   docker run -p 8080:8080 -e ANTHROPIC_API_KEY=... dice-document-pipeline
#
# Run with GPU (local):
#   docker run --gpus all -p 8080:8080 -e ANTHROPIC_API_KEY=... -e USE_DOCLING=true dice-document-pipeline

FROM nvidia/cuda:12.3.2-cudnn9-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    # Keep Hugging Face / Docling model cache inside the image layer
    HF_HOME=/opt/docling-models \
    DOCLING_ARTIFACTS_PATH=/opt/docling-models

# ── System dependencies ────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        software-properties-common \
    && add-apt-repository ppa:deadsnakes/ppa \
    && apt-get update && apt-get install -y --no-install-recommends \
        python3.12 \
        python3.12-dev \
        python3.12-venv \
        python3-pip \
        # pdf2image
        poppler-utils \
        # pytesseract fallback OCR
        tesseract-ocr \
        # pikepdf font embedding
        ghostscript \
        # OpenCV (pulled in by Docling layout models)
        libgl1 \
        libglib2.0-0 \
        # curl for healthcheck debugging
        curl \
        # OpenDataLoader PDF requires Java 21 (spawns a JVM subprocess per job)
        openjdk-21-jre-headless \
    && rm -rf /var/lib/apt/lists/*

RUN update-alternatives --install /usr/bin/python python /usr/bin/python3.12 1 \
    && update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.12 1 \
    && python -m ensurepip --upgrade \
    && python -m pip install --upgrade pip

# ── Python package installation ────────────────────────────────────────────────
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ ./src/

# Install api + docling + gcs extras.
# PyTorch (CPU-only wheel) is pulled in by docling; the CUDA runtime above
# provides the GPU libraries that PyTorch links against at runtime.
RUN pip install -e ".[api,docling,gcs,remediation]"

# ── Pre-bake Docling models ────────────────────────────────────────────────────
# Runs DocumentConverter() once so all transformer model weights are
# downloaded and stored in /opt/docling-models inside the image layer.
# This trades image size (~3 GB) for near-zero cold-start model fetch time.
COPY scripts/bake_docling_models.py ./scripts/bake_docling_models.py
RUN python scripts/bake_docling_models.py

# ── Application files ──────────────────────────────────────────────────────────
COPY config/ ./config/
COPY pipeline/ ./pipeline/

# ── Runtime ───────────────────────────────────────────────────────────────────
EXPOSE 8080

# Cloud Run sets PORT; fall back to 8080 for local runs.
CMD ["sh", "-c", "uvicorn dice_document_pipeline.api.app:app --host 0.0.0.0 --port ${PORT:-8080}"]
