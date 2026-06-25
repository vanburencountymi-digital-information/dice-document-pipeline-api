"""Server-style remediation worker orchestration."""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from .alt_text import generate_alt_text
from .annotation_tag import tag_link_annotations
from .docling_pdf import DoclingAdapter, DoclingPdfAdapter, LocalDoclingStub
from .opendataloader_adapter import LocalTaggingStub, OpenDataLoaderAdapter, TaggingAdapter
from .pdf_finalize import finalize_tagged_pdf
from .remediation_job import RemediationJob, RemediationResult, process_remediation_job
from .storage import LocalStorageAdapter, StorageAdapter


def process_server_remediation_job(
    job: RemediationJob,
    *,
    storage: StorageAdapter | None = None,
    docling: DoclingAdapter | None = None,
    tagging: TaggingAdapter | None = None,
    use_alt_text: bool = True,
    progress: Callable[[str, str], None] | None = None,
) -> RemediationResult:
    """Process a remediation job through adapter boundaries.

    Produces two primary outputs:
      1. Tagged PDF  — OpenDataLoader auto-tagging + pikepdf metadata fixes
      2. HTML + MD   — Docling OCR + layout analysis for WordPress CPT / KB

    Pass real adapter instances for production. Omit to run with local stubs
    (tests and dry runs).
    """
    _progress = progress or (lambda stage, msg: None)

    with tempfile.TemporaryDirectory(prefix=f"dice-remediation-{job.job_id}-") as tmp:
        work_dir = Path(tmp)
        storage_adapter = storage or LocalStorageAdapter(
            Path(job.pipeline_options.get("storage_output_dir", work_dir / "storage"))
        )
        docling_adapter = docling or LocalDoclingStub()
        tagging_adapter = tagging or LocalTaggingStub()

        try:
            _progress("downloading", "Downloading source PDF")
            source_path = storage_adapter.download_pdf(
                job.source_pdf_uri,
                work_dir / "input" / "source.pdf",
            )

            # ── Product 1: HTML + Markdown (Docling) ──────────────────────────
            _progress("converting", "Running Docling — OCR and layout analysis")
            conversion = docling_adapter.convert_pdf(source_path)
            _progress("converted", f"Docling complete — {conversion.pages or '?'} page(s), {conversion.word_count or '?'} words")

            # ── Product 2: Tagged PDF (OpenDataLoader + pikepdf) ──────────────
            _progress("tagging", "Auto-tagging PDF for accessibility (OpenDataLoader)")
            tag_dir = work_dir / "tagged"
            tagged_path = tagging_adapter.tag_pdf(source_path, tag_dir)

            _progress("finalizing", "Applying metadata fixes (Title, tab order, Marked flag)")
            finalize_tagged_pdf(tagged_path, title=job.title)

            # ── DIC-565: alt text for <Figure> elements ────────────────────────
            if use_alt_text:
                _progress("alt_text", "Generating alt text for images via Claude Vision")
                alt_calls = generate_alt_text(tagged_path, document_title=job.title)
                if alt_calls:
                    _progress("alt_text", f"Alt text written for {len(alt_calls)} image(s)")

            # ── DIC-566: tag link annotations in struct tree ───────────────────
            _progress("tagging_annots", "Tagging link annotations in structure tree")
            n_links = tag_link_annotations(tagged_path)
            if n_links:
                _progress("tagging_annots", f"{n_links} link annotation(s) added to struct tree")

            # ── ADA assessment runs on the tagged output ───────────────────────
            _progress("assessing", "Scoring ADA compliance")
            local_options = dict(job.pipeline_options)
            local_options.update(
                {
                    "work_dir": str(work_dir / "package"),
                    "copy_source_to_output": False,
                    "output_dir": str(work_dir / "package" / "remediated"),
                    "log_dir": str(work_dir / "package" / "logs"),
                    # Override source URI so assess_document sees the tagged PDF
                    "_assess_path": str(tagged_path),
                }
            )
            assess_job = replace(
                job,
                source_pdf_uri=tagged_path.as_uri(),
                pipeline_options=local_options,
            )
            result = process_remediation_job(assess_job)

            if result.status == "failed":
                return replace(
                    result,
                    external_api_calls=conversion.api_calls + result.external_api_calls,
                )

            # ── Upload all outputs ─────────────────────────────────────────────
            _progress("uploading", "Uploading results to storage")

            tagged_uri = storage_adapter.upload_pdf(tagged_path, f"{job.job_id}_tagged.pdf")

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
                tagged_pdf_uri=tagged_uri,
                remediated_pdf_uri=tagged_uri,  # same file; remediated_pdf_uri kept for API compat
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
