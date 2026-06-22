"""Pydantic request and response models for the pipeline API."""
from __future__ import annotations

from pydantic import BaseModel


class JobCreatedResponse(BaseModel):
    job_id: str
    status: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    title: str
    document_type: str
    jurisdiction_id: str
    created_at: str
    compliance_score: int | None = None
    compliance_grade: str | None = None
    remediated_pdf_uri: str | None = None
    html_uri: str | None = None
    markdown_uri: str | None = None
    log_uri: str | None = None
    manual_review_items: list[str] = []
    error: str | None = None
