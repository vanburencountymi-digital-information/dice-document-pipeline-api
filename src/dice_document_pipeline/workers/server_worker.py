"""Server-style remediation worker orchestration."""

from __future__ import annotations

import tempfile
from dataclasses import replace
from pathlib import Path

from .docling_pdf import DoclingAdapter, DoclingPdfAdapter, LocalDoclingStub
from .remediation_job import RemediationJob, RemediationResult, process_remediation_job
from .storage import LocalStorageAdapter, StorageAdapter


def process_server_remediation_job(
    job: RemediationJob,
    *,
    storage: StorageAdapter | None = None,
    docling: DoclingAdapter | None = None,
) -> RemediationResult:
    """Process a remediation job through adapter boundaries.

    Docling converts the PDF to HTML and markdown (KB/WP path). The reusable
    remediation package then assesses and scores the original PDF for ADA
    compliance. Both outputs are uploaded to storage and their URIs are
    returned in the result.

    Pass real GCS and DoclingPdfAdapter instances for production. Omit both
    to run against local filesystem with the no-op stub (tests and dry runs).
    """
    with tempfile.TemporaryDirectory(prefix=f"dice-remediation-{job.job_id}-") as tmp:
        work_dir = Path(tmp)
        storage_adapter = storage or LocalStorageAdapter(
            Path(job.pipeline_options.get("storage_output_dir", work_dir / "storage"))
        )
        docling_adapter = docling or LocalDoclingStub()

        try:
            source_path = storage_adapter.download_pdf(
                job.source_pdf_uri,
                work_dir / "input" / "source.pdf",
            )

            # Docling: PDF → HTML + markdown for KB ingestion and WP Document CPT.
            conversion = docling_adapter.convert_pdf(source_path)

            # ADA assessment + scoring runs on the original PDF.
            local_options = dict(job.pipeline_options)
            local_options.update(
                {
                    "work_dir": str(work_dir / "package"),
                    "copy_source_to_output": True,
                    "output_dir": str(work_dir / "package" / "remediated"),
                    "log_dir": str(work_dir / "package" / "logs"),
                }
            )
            local_job = replace(job, pipeline_options=local_options)
            result = process_remediation_job(local_job)

            if result.status == "failed":
                return replace(
                    result,
                    external_api_calls=conversion.api_calls + result.external_api_calls,
                )

            remediated_uri = None
            if result.remediated_pdf_uri:
                remediated_uri = storage_adapter.upload_pdf(
                    _path_from_uri(result.remediated_pdf_uri),
                    f"{job.job_id}.pdf",
                )

            html_uri = None
            if conversion.html_content:
                html_path = _write_text(
                    work_dir / "docling" / f"{job.job_id}.html",
                    conversion.html_content,
                )
                html_uri = storage_adapter.upload_html(html_path, f"{job.job_id}.html")

            markdown_uri = None
            if conversion.markdown_content:
                md_path = _write_text(
                    work_dir / "docling" / f"{job.job_id}.md",
                    conversion.markdown_content,
                )
                markdown_uri = storage_adapter.upload_markdown(md_path, f"{job.job_id}.md")

            log_uri = None
            if result.log_uri:
                log_uri = storage_adapter.upload_log(
                    _path_from_uri(result.log_uri),
                    f"{job.job_id}.txt",
                )

            return replace(
                result,
                remediated_pdf_uri=remediated_uri,
                html_uri=html_uri,
                markdown_uri=markdown_uri,
                log_uri=log_uri,
                external_api_calls=conversion.api_calls + result.external_api_calls,
            )
        except Exception as exc:
            return RemediationResult(
                job_id=job.job_id,
                status="failed",
                pipeline_version=str(
                    job.pipeline_options.get("pipeline_version", "package-refactor-alpha")
                ),
                error=str(exc),
            )


def _write_text(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _path_from_uri(uri: str) -> Path:
    if not uri.startswith("file:"):
        return Path(uri).resolve()
    from .storage import local_path_from_uri

    return local_path_from_uri(uri)
