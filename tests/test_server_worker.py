from pathlib import Path
from unittest.mock import patch

from dice_document_pipeline.workers import (
    LocalAdobePdfServicesStub,
    LocalStorageAdapter,
    RemediationJob,
    process_server_remediation_job,
)


def complete_issues(**overrides):
    issues = {
        "has_structure_tree": True,
        "has_text_layer": True,
        "has_images": False,
        "images_without_alt": 0,
        "has_forms": False,
        "form_fields_count": 0,
        "form_fields_without_tooltips": 0,
        "has_bookmarks": True,
        "page_count": 1,
        "unembedded_fonts": [],
        "vague_links": [],
        "has_tables": False,
        "tables_without_headers": 0,
        "has_headings": True,
        "contrast_check_passed": True,
    }
    issues.update(overrides)
    return issues


def test_server_worker_runs_storage_adobe_and_package_boundaries(tmp_path: Path):
    source_pdf = tmp_path / "source.pdf"
    source_pdf.write_bytes(b"%PDF-1.4\n")
    storage = LocalStorageAdapter(tmp_path / "storage")
    adobe = LocalAdobePdfServicesStub()
    job = RemediationJob(
        job_id="job-156",
        archival_object_id="archive-156",
        source_pdf_uri=str(source_pdf),
        title="Source",
        document_type="minutes",
        jurisdiction_id="jurisdiction-1",
        pipeline_options={
            "county_name": "Test County",
            "log_date": "2026-05-27",
        },
    )

    with patch(
        "dice_document_pipeline.workers.remediation_job.assess_document",
        return_value=complete_issues(),
    ):
        result = process_server_remediation_job(job, storage=storage, adobe=adobe)

    assert result.status == "complete"
    assert result.compliance_score == 100
    assert result.compliance_grade == "A"
    assert result.remediated_pdf_uri is not None
    assert result.log_uri is not None
    assert len(result.external_api_calls) == 1
    assert result.external_api_calls[0]["mode"] == "local_stub"
    assert (tmp_path / "storage" / "pdfs" / "job-156.pdf").exists()
    assert (tmp_path / "storage" / "logs" / "job-156.txt").exists()


def test_server_worker_returns_failed_result_when_storage_download_fails(tmp_path: Path):
    storage = LocalStorageAdapter(tmp_path / "storage")
    job = RemediationJob(
        job_id="job-missing",
        archival_object_id="archive-missing",
        source_pdf_uri=str(tmp_path / "missing.pdf"),
        title="Missing",
        document_type="minutes",
        jurisdiction_id="jurisdiction-1",
    )

    result = process_server_remediation_job(job, storage=storage)

    assert result.status == "failed"
    assert result.error is not None
    assert "does not exist" in result.error
