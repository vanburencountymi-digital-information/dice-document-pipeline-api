"""Server-style remediation worker orchestration."""

from __future__ import annotations

import tempfile
from dataclasses import replace
from pathlib import Path

from .adobe_pdf_services import AdobePdfServicesAdapter, LocalAdobePdfServicesStub
from .remediation_job import RemediationJob, RemediationResult, process_remediation_job
from .storage import LocalStorageAdapter, StorageAdapter


def process_server_remediation_job(
    job: RemediationJob,
    *,
    storage: StorageAdapter | None = None,
    adobe: AdobePdfServicesAdapter | None = None,
) -> RemediationResult:
    """Process a remediation job through adapter boundaries.

    This is the first DIC-156 shape: storage and Adobe are interfaces, while
    the reusable remediation package remains the core processing boundary.
    """
    with tempfile.TemporaryDirectory(prefix=f"dice-remediation-{job.job_id}-") as tmp:
        work_dir = Path(tmp)
        storage_adapter = storage or LocalStorageAdapter(
            Path(job.pipeline_options.get("storage_output_dir", work_dir / "storage"))
        )
        adobe_adapter = adobe or LocalAdobePdfServicesStub()

        try:
            source_path = storage_adapter.download_pdf(
                job.source_pdf_uri,
                work_dir / "input" / "source.pdf",
            )
            autotag_result = adobe_adapter.autotag_pdf(
                source_path,
                work_dir / "adobe" / "autotagged.pdf",
            )

            local_options = dict(job.pipeline_options)
            local_options.update(
                {
                    "work_dir": str(work_dir / "package"),
                    "copy_source_to_output": True,
                    "output_dir": str(work_dir / "package" / "remediated"),
                    "log_dir": str(work_dir / "package" / "logs"),
                }
            )
            local_job = replace(
                job,
                source_pdf_uri=str(autotag_result.output_pdf),
                pipeline_options=local_options,
            )
            result = process_remediation_job(local_job)
            if result.status == "failed":
                return replace(
                    result,
                    external_api_calls=autotag_result.api_calls + result.external_api_calls,
                )

            remediated_uri = None
            if result.remediated_pdf_uri:
                remediated_uri = storage_adapter.upload_pdf(
                    _path_from_file_uri(result.remediated_pdf_uri),
                    f"{job.job_id}.pdf",
                )

            log_uri = None
            if result.log_uri:
                log_uri = storage_adapter.upload_log(
                    _path_from_file_uri(result.log_uri),
                    f"{job.job_id}.txt",
                )

            return replace(
                result,
                remediated_pdf_uri=remediated_uri,
                log_uri=log_uri,
                external_api_calls=autotag_result.api_calls + result.external_api_calls,
            )
        except Exception as exc:
            return RemediationResult(
                job_id=job.job_id,
                status="failed",
                pipeline_version=str(job.pipeline_options.get("pipeline_version", "package-refactor-alpha")),
                error=str(exc),
            )


def _path_from_file_uri(uri: str) -> Path:
    if not uri.startswith("file:"):
        return Path(uri).resolve()
    from .storage import local_path_from_uri

    return local_path_from_uri(uri)
