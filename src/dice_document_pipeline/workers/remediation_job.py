"""Typed boundary for the future server remediation worker."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


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
    compliance_score: int | None = None
    compliance_grade: str | None = None
    manual_review_items: list[str] = field(default_factory=list)
    log_uri: str | None = None
    pipeline_version: str | None = None
    external_api_calls: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


def process_remediation_job(job: RemediationJob) -> RemediationResult:
    """Future server-worker entry point.

    The desktop CSV pipeline remains the operational implementation for now.
    This placeholder gives the DICE Tool API and GCP worker workstreams a
    stable contract to build toward without coupling them to local CSV files.
    """
    raise NotImplementedError(
        "Server remediation worker is not implemented yet; "
        f"received job {job.job_id!r} for archival object {job.archival_object_id!r}."
    )
