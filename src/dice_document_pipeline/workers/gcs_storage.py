"""Google Cloud Storage adapter for the DICE document pipeline.

Implements the StorageAdapter Protocol against GCS buckets. Lazy-imports
google-cloud-storage so the package loads without it on machines that don't
need GCS (local dev, Windows desktop pipeline).

Authentication uses Application Default Credentials — on Cloud Run this is
the service account identity; locally run `gcloud auth application-default login`.

Usage
-----
    from dice_document_pipeline.workers.gcs_storage import GcsStorageAdapter

    storage = GcsStorageAdapter(bucket_name="dice-pipeline-artifacts", prefix="jobs")
    # source_uri may point to any gs:// bucket (e.g. an inbound upload bucket)
    source_path = storage.download_pdf("gs://dice-uploads/inbound/abc.pdf", work_dir / "source.pdf")
    uri = storage.upload_pdf(remediated_path, "job-123.pdf")
    # -> "gs://dice-pipeline-artifacts/jobs/pdfs/job-123.pdf"
"""

from __future__ import annotations

from pathlib import Path


_CONTENT_TYPES: dict[str, str] = {
    "pdf": "application/pdf",
    "log": "text/plain; charset=utf-8",
    "html": "text/html; charset=utf-8",
    "markdown": "text/markdown; charset=utf-8",
}


class GcsStorageAdapter:
    """GCS-backed storage adapter.

    All uploads go to `gs://<bucket_name>/<prefix>/<subdir>/<name>`.
    Downloads parse the bucket name from the source URI, so the source
    PDF can live in a different bucket (e.g. a separate inbound bucket).
    """

    def __init__(
        self,
        bucket_name: str,
        prefix: str = "",
        *,
        client=None,
    ):
        self._bucket_name = bucket_name
        self._prefix = prefix.rstrip("/") if prefix else ""
        self._client = client or _build_gcs_client()

    # ------------------------------------------------------------------
    # StorageAdapter Protocol
    # ------------------------------------------------------------------

    def download_pdf(self, source_uri: str, destination: Path) -> Path:
        """Download a PDF from any gs:// URI to a local destination path."""
        src_bucket_name, blob_name = _parse_gs_uri(source_uri)
        bucket = self._client.bucket(src_bucket_name)
        blob = bucket.blob(blob_name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        blob.download_to_filename(str(destination))
        return destination

    def upload_pdf(self, source: Path, destination_name: str) -> str:
        return self._upload(source, "pdfs", destination_name, _CONTENT_TYPES["pdf"])

    def upload_log(self, source: Path, destination_name: str) -> str:
        return self._upload(source, "logs", destination_name, _CONTENT_TYPES["log"])

    def upload_html(self, source: Path, destination_name: str) -> str:
        return self._upload(source, "html", destination_name, _CONTENT_TYPES["html"])

    def upload_markdown(self, source: Path, destination_name: str) -> str:
        return self._upload(source, "markdown", destination_name, _CONTENT_TYPES["markdown"])

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _blob_name(self, subdir: str, filename: str) -> str:
        parts = [p for p in [self._prefix, subdir, filename] if p]
        return "/".join(parts)

    def _upload(
        self, source: Path, subdir: str, destination_name: str, content_type: str
    ) -> str:
        blob_name = self._blob_name(subdir, destination_name)
        blob = self._client.bucket(self._bucket_name).blob(blob_name)
        blob.upload_from_filename(str(source), content_type=content_type)
        return f"gs://{self._bucket_name}/{blob_name}"


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _parse_gs_uri(uri: str) -> tuple[str, str]:
    """Parse a gs://bucket/blob URI into (bucket_name, blob_name)."""
    if not uri.startswith("gs://"):
        raise ValueError(f"Expected a gs:// URI, got: {uri!r}")
    remainder = uri[5:]
    slash = remainder.find("/")
    if slash <= 0 or slash == len(remainder) - 1:
        raise ValueError(f"Invalid GCS URI (missing bucket or blob path): {uri!r}")
    return remainder[:slash], remainder[slash + 1:]


def _build_gcs_client():
    try:
        from google.cloud import storage  # type: ignore[import-untyped]

        return storage.Client()
    except ImportError as exc:
        raise ImportError(
            "google-cloud-storage is not installed. "
            "Install with `pip install 'dice-document-pipeline[gcs]'`."
        ) from exc
