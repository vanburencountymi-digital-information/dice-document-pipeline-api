"""Tests for the FastAPI application.

TestClient runs background tasks synchronously, so tests can assert on
final job state after a POST /jobs call without sleeping or polling.
"""
from __future__ import annotations

import io
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import dice_document_pipeline.api.app as api_module
from dice_document_pipeline.api.app import app
from dice_document_pipeline.api.store import InMemoryJobStore
from dice_document_pipeline.workers import RemediationResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MINIMAL_PDF = b"%PDF-1.4\n1 0 obj<</Type /Catalog>>endobj\nxref\n0 1\n%%EOF"

COMPLETE_RESULT = RemediationResult(
    job_id="REPLACED",          # patched to real job_id in tests that need it
    status="complete",
    compliance_score=95,
    compliance_grade="A",
    remediated_pdf_uri="file:///tmp/job.pdf",
    html_uri="file:///tmp/job.html",
    markdown_uri="file:///tmp/job.md",
    log_uri="file:///tmp/job.txt",
    pipeline_version="test",
)


@pytest.fixture(autouse=True)
def fresh_store(monkeypatch):
    """Reset the module-level job store before every test."""
    monkeypatch.setattr(api_module, "job_store", InMemoryJobStore())


@pytest.fixture()
def client():
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


def _upload(client, *, filename="doc.pdf", extra_data=None):
    data = {
        "title": "June Minutes",
        "document_type": "minutes",
        "jurisdiction_id": "vbc-001",
        **(extra_data or {}),
    }
    files = {"file": (filename, io.BytesIO(MINIMAL_PDF), "application/pdf")}
    return client.post("/jobs", data=data, files=files)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

def test_health_returns_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# POST /jobs
# ---------------------------------------------------------------------------

def test_create_job_returns_202_and_job_id(client):
    with patch(
        "dice_document_pipeline.api.app.process_server_remediation_job",
        return_value=COMPLETE_RESULT,
    ):
        r = _upload(client)

    assert r.status_code == 202
    body = r.json()
    assert "job_id" in body
    assert body["status"] == "queued"


def test_create_job_rejects_non_pdf(client):
    files = {"file": ("report.docx", io.BytesIO(b"not a pdf"), "application/octet-stream")}
    r = client.post(
        "/jobs",
        data={"title": "T", "document_type": "minutes", "jurisdiction_id": "vbc-001"},
        files=files,
    )
    assert r.status_code == 422
    assert "PDF" in r.json()["detail"]


def test_create_job_background_task_runs_synchronously(client):
    """TestClient runs background tasks before returning — final state is visible."""
    with patch(
        "dice_document_pipeline.api.app.process_server_remediation_job",
        return_value=COMPLETE_RESULT,
    ):
        post_r = _upload(client)
        job_id = post_r.json()["job_id"]
        get_r = client.get(f"/jobs/{job_id}")

    assert get_r.status_code == 200
    body = get_r.json()
    assert body["status"] == "complete"
    assert body["compliance_score"] == 95
    assert body["compliance_grade"] == "A"


def test_create_job_stores_metadata(client):
    with patch(
        "dice_document_pipeline.api.app.process_server_remediation_job",
        return_value=COMPLETE_RESULT,
    ):
        post_r = _upload(client)
        job_id = post_r.json()["job_id"]
        get_r = client.get(f"/jobs/{job_id}")

    body = get_r.json()
    assert body["title"] == "June Minutes"
    assert body["document_type"] == "minutes"
    assert body["jurisdiction_id"] == "vbc-001"
    assert body["created_at"]  # non-empty ISO timestamp


def test_create_job_uses_provided_archival_object_id(client):
    with patch(
        "dice_document_pipeline.api.app.process_server_remediation_job"
    ) as mock_run:
        mock_run.return_value = COMPLETE_RESULT
        _upload(client, extra_data={"archival_object_id": "archive-xyz"})
        submitted_job = mock_run.call_args[0][0]

    assert submitted_job.archival_object_id == "archive-xyz"


def test_create_job_defaults_archival_object_id_to_job_id(client):
    with patch(
        "dice_document_pipeline.api.app.process_server_remediation_job"
    ) as mock_run:
        mock_run.return_value = COMPLETE_RESULT
        post_r = _upload(client)
        job_id = post_r.json()["job_id"]
        submitted_job = mock_run.call_args[0][0]

    assert submitted_job.archival_object_id == job_id


# ---------------------------------------------------------------------------
# GET /jobs/{job_id}
# ---------------------------------------------------------------------------

def test_get_job_returns_404_for_unknown_id(client):
    r = client.get("/jobs/does-not-exist")
    assert r.status_code == 404


def test_get_job_returns_all_result_fields(client):
    with patch(
        "dice_document_pipeline.api.app.process_server_remediation_job",
        return_value=COMPLETE_RESULT,
    ):
        post_r = _upload(client)
        job_id = post_r.json()["job_id"]
        r = client.get(f"/jobs/{job_id}")

    body = r.json()
    assert body["remediated_pdf_uri"] == "file:///tmp/job.pdf"
    assert body["html_uri"] == "file:///tmp/job.html"
    assert body["markdown_uri"] == "file:///tmp/job.md"
    assert body["log_uri"] == "file:///tmp/job.txt"


def test_get_job_reflects_failed_status_on_exception(client):
    with patch(
        "dice_document_pipeline.api.app.process_server_remediation_job",
        side_effect=RuntimeError("Docling exploded"),
    ):
        post_r = _upload(client)
        job_id = post_r.json()["job_id"]
        r = client.get(f"/jobs/{job_id}")

    body = r.json()
    assert body["status"] == "failed"
    assert "Docling exploded" in body["error"]


def test_get_job_reflects_manual_review_status(client):
    review_result = RemediationResult(
        job_id="x",
        status="manual_review",
        compliance_score=61,
        compliance_grade="D",
        manual_review_items=["Missing alt text on 3 images"],
        pipeline_version="test",
    )
    with patch(
        "dice_document_pipeline.api.app.process_server_remediation_job",
        return_value=review_result,
    ):
        post_r = _upload(client)
        job_id = post_r.json()["job_id"]
        r = client.get(f"/jobs/{job_id}")

    body = r.json()
    assert body["status"] == "manual_review"
    assert body["manual_review_items"] == ["Missing alt text on 3 images"]
