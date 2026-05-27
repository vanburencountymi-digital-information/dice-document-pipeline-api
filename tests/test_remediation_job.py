from pathlib import Path
from unittest.mock import patch

from dice_document_pipeline.workers.remediation_job import (
    RemediationJob,
    process_remediation_job,
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


def test_process_remediation_job_scores_and_writes_log(tmp_path: Path):
    source_pdf = tmp_path / "source.pdf"
    source_pdf.write_bytes(b"%PDF-1.4\n")
    job = RemediationJob(
        job_id="job-1",
        archival_object_id="archive-1",
        source_pdf_uri=str(source_pdf),
        title="Source",
        document_type="minutes",
        jurisdiction_id="jurisdiction-1",
        pipeline_options={
            "work_dir": str(tmp_path),
            "county_name": "Test County",
            "log_date": "2026-05-27",
        },
    )

    with patch(
        "dice_document_pipeline.workers.remediation_job.assess_document",
        return_value=complete_issues(),
    ):
        result = process_remediation_job(job)

    assert result.status == "complete"
    assert result.compliance_score == 100
    assert result.compliance_grade == "A"
    assert result.remediated_pdf_uri is not None
    assert result.log_uri is not None
    assert (tmp_path / "logs" / "job-1.txt").exists()
    assert (tmp_path / "remediated" / "source.pdf").exists()


def test_process_remediation_job_marks_manual_review_when_score_has_items(tmp_path: Path):
    source_pdf = tmp_path / "source.pdf"
    source_pdf.write_bytes(b"%PDF-1.4\n")
    job = RemediationJob(
        job_id="job-2",
        archival_object_id="archive-2",
        source_pdf_uri=source_pdf.as_uri(),
        title="Source",
        document_type="minutes",
        jurisdiction_id="jurisdiction-1",
        pipeline_options={"work_dir": str(tmp_path)},
    )

    with patch(
        "dice_document_pipeline.workers.remediation_job.assess_document",
        return_value=complete_issues(has_text_layer=False),
    ):
        result = process_remediation_job(job)

    assert result.status == "manual_review"
    assert result.compliance_score == 80
    assert result.manual_review_items


def test_process_remediation_job_returns_failed_result_for_missing_source(tmp_path: Path):
    job = RemediationJob(
        job_id="job-3",
        archival_object_id="archive-3",
        source_pdf_uri=str(tmp_path / "missing.pdf"),
        title="Missing",
        document_type="minutes",
        jurisdiction_id="jurisdiction-1",
        pipeline_options={"work_dir": str(tmp_path)},
    )

    result = process_remediation_job(job)

    assert result.status == "failed"
    assert result.error is not None
    assert "does not exist" in result.error
