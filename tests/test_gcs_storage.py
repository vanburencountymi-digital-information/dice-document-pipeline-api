from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

from dice_document_pipeline.workers.gcs_storage import GcsStorageAdapter, _parse_gs_uri


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_adapter(bucket_name="dice-artifacts", prefix="", client=None):
    mock_client = client or MagicMock()
    adapter = GcsStorageAdapter(bucket_name, prefix, client=mock_client)
    return adapter, mock_client


# ---------------------------------------------------------------------------
# _parse_gs_uri
# ---------------------------------------------------------------------------

class TestParseGsUri:
    def test_valid_uri(self):
        bucket, blob = _parse_gs_uri("gs://my-bucket/path/to/file.pdf")
        assert bucket == "my-bucket"
        assert blob == "path/to/file.pdf"

    def test_single_segment_blob(self):
        bucket, blob = _parse_gs_uri("gs://my-bucket/file.pdf")
        assert bucket == "my-bucket"
        assert blob == "file.pdf"

    def test_rejects_non_gs_uri(self):
        with pytest.raises(ValueError, match="gs://"):
            _parse_gs_uri("s3://bucket/file.pdf")

    def test_rejects_missing_blob(self):
        with pytest.raises(ValueError, match="missing bucket or blob"):
            _parse_gs_uri("gs://bucket/")

    def test_rejects_bucket_only(self):
        with pytest.raises(ValueError, match="missing bucket or blob"):
            _parse_gs_uri("gs://bucket")


# ---------------------------------------------------------------------------
# Uploads
# ---------------------------------------------------------------------------

class TestUploads:
    def test_upload_pdf_returns_gs_uri(self, tmp_path):
        source = tmp_path / "doc.pdf"
        source.write_bytes(b"%PDF-1.4")
        adapter, mock_client = make_adapter(bucket_name="dice-out", prefix="jobs")

        uri = adapter.upload_pdf(source, "job-1.pdf")

        assert uri == "gs://dice-out/jobs/pdfs/job-1.pdf"
        blob = mock_client.bucket("dice-out").blob("jobs/pdfs/job-1.pdf")
        blob.upload_from_filename.assert_called_once_with(
            str(source), content_type="application/pdf"
        )

    def test_upload_log_returns_gs_uri(self, tmp_path):
        source = tmp_path / "job-1.txt"
        source.write_text("log content", encoding="utf-8")
        adapter, mock_client = make_adapter(prefix="jobs")

        uri = adapter.upload_log(source, "job-1.txt")

        assert uri == "gs://dice-artifacts/jobs/logs/job-1.txt"
        blob = mock_client.bucket("dice-artifacts").blob("jobs/logs/job-1.txt")
        blob.upload_from_filename.assert_called_once_with(
            str(source), content_type="text/plain; charset=utf-8"
        )

    def test_upload_html_uses_html_content_type(self, tmp_path):
        source = tmp_path / "job-1.html"
        source.write_text("<html/>", encoding="utf-8")
        adapter, mock_client = make_adapter(prefix="jobs")

        uri = adapter.upload_html(source, "job-1.html")

        assert uri == "gs://dice-artifacts/jobs/html/job-1.html"
        blob = mock_client.bucket("dice-artifacts").blob("jobs/html/job-1.html")
        blob.upload_from_filename.assert_called_once_with(
            str(source), content_type="text/html; charset=utf-8"
        )

    def test_upload_markdown_uses_markdown_content_type(self, tmp_path):
        source = tmp_path / "job-1.md"
        source.write_text("# Doc", encoding="utf-8")
        adapter, mock_client = make_adapter(prefix="jobs")

        uri = adapter.upload_markdown(source, "job-1.md")

        assert uri == "gs://dice-artifacts/jobs/markdown/job-1.md"
        blob = mock_client.bucket("dice-artifacts").blob("jobs/markdown/job-1.md")
        blob.upload_from_filename.assert_called_once_with(
            str(source), content_type="text/markdown; charset=utf-8"
        )

    def test_upload_without_prefix_omits_prefix_segment(self, tmp_path):
        source = tmp_path / "doc.pdf"
        source.write_bytes(b"%PDF-1.4")
        adapter, mock_client = make_adapter(bucket_name="dice-out", prefix="")

        uri = adapter.upload_pdf(source, "job-2.pdf")

        assert uri == "gs://dice-out/pdfs/job-2.pdf"
        blob = mock_client.bucket("dice-out").blob("pdfs/job-2.pdf")
        blob.upload_from_filename.assert_called_once()


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

class TestDownload:
    def test_download_pdf_writes_to_destination(self, tmp_path):
        destination = tmp_path / "work" / "source.pdf"
        adapter, mock_client = make_adapter()

        result = adapter.download_pdf(
            "gs://dice-uploads/inbound/abc.pdf", destination
        )

        assert result == destination
        assert destination.parent.exists()
        blob = mock_client.bucket("dice-uploads").blob("inbound/abc.pdf")
        blob.download_to_filename.assert_called_once_with(str(destination))

    def test_download_creates_parent_directories(self, tmp_path):
        destination = tmp_path / "deep" / "nested" / "source.pdf"
        adapter, mock_client = make_adapter()

        adapter.download_pdf("gs://bucket/blob.pdf", destination)

        assert destination.parent.exists()

    def test_download_uses_source_uri_bucket_not_output_bucket(self, tmp_path):
        destination = tmp_path / "source.pdf"
        adapter, mock_client = make_adapter(bucket_name="output-bucket")

        adapter.download_pdf("gs://inbound-bucket/file.pdf", destination)

        mock_client.bucket.assert_any_call("inbound-bucket")

    def test_download_rejects_non_gs_uri(self, tmp_path):
        adapter, _ = make_adapter()
        with pytest.raises(ValueError, match="gs://"):
            adapter.download_pdf("https://example.com/file.pdf", tmp_path / "out.pdf")


# ---------------------------------------------------------------------------
# Blob name construction
# ---------------------------------------------------------------------------

class TestBlobName:
    def test_prefix_prepended_to_subdir_and_filename(self):
        adapter, _ = make_adapter(prefix="v1/jobs")
        assert adapter._blob_name("pdfs", "job-1.pdf") == "v1/jobs/pdfs/job-1.pdf"

    def test_empty_prefix_omitted(self):
        adapter, _ = make_adapter(prefix="")
        assert adapter._blob_name("pdfs", "job-1.pdf") == "pdfs/job-1.pdf"

    def test_trailing_slash_stripped_from_prefix(self):
        adapter, _ = make_adapter(prefix="jobs/")
        assert adapter._blob_name("pdfs", "job-1.pdf") == "jobs/pdfs/job-1.pdf"
