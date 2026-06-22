"""Settings loaded from environment variables (or .env file).

Production overrides (Cloud Run env vars):
    USE_GCS=true
    GCS_INBOUND_BUCKET=dice-uploads
    GCS_ARTIFACTS_BUCKET=dice-pipeline-artifacts
    GCS_PREFIX=jobs
    USE_DOCLING=true

Local dev defaults keep everything on the local filesystem with the
stub adapters so no cloud credentials are needed.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    use_gcs: bool = False
    gcs_inbound_bucket: str = ""
    gcs_artifacts_bucket: str = ""
    gcs_prefix: str = "jobs"

    use_docling: bool = False

    # Local filesystem root used when use_gcs=false.
    storage_output_dir: str = "/tmp/dice-pipeline"

    # CORS origins accepted by the API. Comma-separated list or "*".
    cors_origins: str = "*"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
