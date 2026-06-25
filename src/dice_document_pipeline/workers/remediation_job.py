"""Typed boundary for the future server remediation worker."""

from __future__ import annotations

import datetime
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from dice_document_pipeline.remediation.assessment import assess_document
from dice_document_pipeline.remediation.logs import write_log
from dice_document_pipeline.remediation.scoring import score_document


DEFAULT_PIPELINE_VERSION = "package-refactor-alpha"


@dataclass(frozen=True)
class RemediationJob:
    """Input contract for a future queue-driven remediation job."""

    job_id: str
    archival_object_id: str
    source_pdf_uri: str
    title: str
    document_type: str
    jurisdiction_id: str
    sensitivity_class: str = "public"
    exclude_from_kb: bool = False
    pipeline_options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RemediationResult:
    """Structured output contract for a completed remediation job."""

    job_id: str
    status: str
    remediated_pdf_uri: str | None = None
    tagged_pdf_uri: str | None = None
    html_uri: str | None = None
    markdown_uri: str | None = None
    compliance_score: int | None = None
    compliance_grade: str | None = None
    page_count: int | None = None
    manual_review_items: list[str] = field(default_factory=list)
    log_uri: str | None = None
    pipeline_version: str | None = None
    external_api_calls: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


def process_remediation_job(job: RemediationJob) -> RemediationResult:
    """Process one remediation job through the reusable package boundary.

    This first worker slice intentionally handles local filesystem inputs only.
    Queue polling, object storage, Adobe PDF Services, and Cloud Run callbacks
    belong to the DIC-156 infrastructure work. This function gives those layers
    a concrete, testable package entry point without depending on desktop CSV
    state.
    """
    try:
        source_path = _local_path_from_uri(job.source_pdf_uri)
        if not source_path.exists():
            return _failed_result(
                job,
                f"Source PDF does not exist: {source_path}",
            )
        if not source_path.is_file():
            return _failed_result(
                job,
                f"Source PDF is not a file: {source_path}",
            )

        work_dir = Path(job.pipeline_options.get("work_dir", ".")).resolve()
        log_dir = Path(job.pipeline_options.get("log_dir", work_dir / "logs")).resolve()
        output_dir = Path(
            job.pipeline_options.get("output_dir", work_dir / "remediated")
        ).resolve()
        county_name = str(job.pipeline_options.get("county_name", "DICE"))
        log_date = str(
            job.pipeline_options.get("log_date", datetime.date.today().isoformat())
        )
        pipeline_version = str(
            job.pipeline_options.get("pipeline_version", DEFAULT_PIPELINE_VERSION)
        )

        log_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)

        issues = assess_document(source_path)
        page_count = int(issues.get("page_count", 0) or 0)
        score, grade, manual_items = score_document(issues, page_count)

        log_path = log_dir / f"{job.job_id}.txt"
        write_log(
            log_path,
            filename=source_path.name,
            issues=issues,
            fixes=job.pipeline_options.get("fixes", []),
            alt_log_entries=job.pipeline_options.get("alt_log_entries", []),
            manual_items=manual_items,
            score=score,
            grade=grade,
            county_name=county_name,
            log_date=log_date,
        )

        remediated_pdf_uri = None
        if job.pipeline_options.get("copy_source_to_output", True):
            output_path = output_dir / source_path.name
            if source_path.resolve() != output_path.resolve():
                shutil.copy2(source_path, output_path)
            remediated_pdf_uri = output_path.as_uri()

        status = "complete" if not manual_items else "manual_review"
        return RemediationResult(
            job_id=job.job_id,
            status=status,
            remediated_pdf_uri=remediated_pdf_uri,
            compliance_score=score,
            compliance_grade=grade,
            page_count=page_count,
            manual_review_items=manual_items,
            log_uri=log_path.as_uri(),
            pipeline_version=pipeline_version,
            external_api_calls=[],
        )
    except Exception as exc:
        return _failed_result(job, str(exc))


def _local_path_from_uri(uri: str) -> Path:
    """Resolve a local path or file URI to a filesystem path."""
    if os.name == "nt" and _is_windows_drive_path(uri):
        return Path(uri).expanduser().resolve()

    parsed = urlparse(uri)
    if parsed.scheme in ("", None):
        return Path(uri).expanduser().resolve()
    if parsed.scheme != "file":
        raise ValueError(
            f"Unsupported source_pdf_uri scheme {parsed.scheme!r}; "
            "this worker slice supports local paths and file:// URIs only."
        )
    if parsed.netloc and parsed.netloc not in ("localhost", "127.0.0.1"):
        raise ValueError(f"Unsupported file URI host: {parsed.netloc}")
    path = unquote(parsed.path)
    if os.name == "nt" and _is_windows_drive_file_uri_path(path):
        path = path[1:]
    return Path(path).resolve()


def _is_windows_drive_file_uri_path(path: str) -> bool:
    return len(path) >= 4 and path[0] == "/" and path[2] == ":" and path[3] == "/"


def _is_windows_drive_path(path: str) -> bool:
    return len(path) >= 3 and path[1] == ":" and path[2] in ("\\", "/")


def _failed_result(job: RemediationJob, error: str) -> RemediationResult:
    return RemediationResult(
        job_id=job.job_id,
        status="failed",
        pipeline_version=str(
            job.pipeline_options.get("pipeline_version", DEFAULT_PIPELINE_VERSION)
        ),
        error=error,
    )
