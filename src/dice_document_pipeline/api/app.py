"""DICE Document Pipeline — FastAPI application.

Run locally:
    uvicorn dice_document_pipeline.api.app:app --reload

Production (Cloud Run):
    Set USE_GCS=true, GCS_INBOUND_BUCKET, GCS_ARTIFACTS_BUCKET, USE_DOCLING=true
    in the Cloud Run environment. Authentication uses Application Default Credentials.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from dice_document_pipeline.workers import (
    LocalDoclingStub,
    LocalStorageAdapter,
    RemediationJob,
    process_server_remediation_job,
)

from .config import Settings
from .models import JobCreatedResponse, JobStatusResponse
from .store import InMemoryJobStore, JobRecord

settings = Settings()
job_store = InMemoryJobStore()

app = FastAPI(title="DICE Document Pipeline API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins.split(",") if settings.cors_origins != "*" else ["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ------------------------------------------------------------------
# Adapter factories — swap via env vars
# ------------------------------------------------------------------

def _build_storage():
    if settings.use_gcs:
        from dice_document_pipeline.workers import GcsStorageAdapter
        return GcsStorageAdapter(
            settings.gcs_artifacts_bucket,
            prefix=settings.gcs_prefix,
        )
    return LocalStorageAdapter(Path(settings.storage_output_dir) / "artifacts")


def _build_docling():
    if settings.use_docling:
        from dice_document_pipeline.workers.docling_pdf import DoclingPdfAdapter
        return DoclingPdfAdapter()
    return LocalDoclingStub()


# ------------------------------------------------------------------
# Routes
# ------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/jobs", response_model=JobCreatedResponse, status_code=202)
async def create_job(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="PDF to remediate"),
    title: str = Form(...),
    document_type: str = Form(...),
    jurisdiction_id: str = Form(...),
    archival_object_id: str = Form(""),
    sensitivity_class: str = Form("public"),
):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=422, detail="Uploaded file must be a PDF.")

    job_id = str(uuid.uuid4())
    if not archival_object_id:
        archival_object_id = job_id

    # Stage the upload locally, then hand off to inbound storage.
    inbound_dir = Path(settings.storage_output_dir) / "inbound"
    inbound_dir.mkdir(parents=True, exist_ok=True)
    staged_path = inbound_dir / f"{job_id}.pdf"
    staged_path.write_bytes(await file.read())

    if settings.use_gcs and settings.gcs_inbound_bucket:
        from dice_document_pipeline.workers import GcsStorageAdapter
        source_pdf_uri = GcsStorageAdapter(settings.gcs_inbound_bucket).upload_pdf(
            staged_path, f"{job_id}.pdf"
        )
        staged_path.unlink(missing_ok=True)
    else:
        source_pdf_uri = str(staged_path)

    job = RemediationJob(
        job_id=job_id,
        archival_object_id=archival_object_id,
        source_pdf_uri=source_pdf_uri,
        title=title,
        document_type=document_type,
        jurisdiction_id=jurisdiction_id,
        sensitivity_class=sensitivity_class,
    )
    record = JobRecord(
        job_id=job_id,
        status="queued",
        title=title,
        document_type=document_type,
        jurisdiction_id=jurisdiction_id,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    job_store.create(record)
    background_tasks.add_task(_run_job, job)
    return JobCreatedResponse(job_id=job_id, status="queued")


@app.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str):
    record = job_store.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found.")
    return JobStatusResponse(**record.__dict__)


# ------------------------------------------------------------------
# Background worker
# ------------------------------------------------------------------

def _run_job(job: RemediationJob) -> None:
    job_store.update_status(job.job_id, "running")
    try:
        result = process_server_remediation_job(
            job,
            storage=_build_storage(),
            docling=_build_docling(),
        )
        job_store.update_from_result(job.job_id, result)
    except Exception as exc:
        job_store.set_failed(job.job_id, str(exc))
