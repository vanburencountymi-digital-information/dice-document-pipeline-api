"""In-process job store — thread-safe dict backed by a lock.

This is the MVP state layer. Replace with a PostgreSQL-backed store when
pg-boss job chaining is wired in (DIC-266).
"""
from __future__ import annotations

import queue as _queue
import threading
from dataclasses import dataclass, field
from typing import Any

from dice_document_pipeline.workers import RemediationResult


@dataclass
class JobRecord:
    job_id: str
    status: str          # queued | running | complete | manual_review | failed
    title: str
    document_type: str
    jurisdiction_id: str
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    processing_seconds: float | None = None
    page_count: int | None = None
    compliance_score: int | None = None
    compliance_grade: str | None = None
    remediated_pdf_uri: str | None = None
    tagged_pdf_uri: str | None = None
    html_uri: str | None = None
    markdown_uri: str | None = None
    log_uri: str | None = None
    manual_review_items: list[str] = field(default_factory=list)
    external_api_calls: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


class InMemoryJobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, JobRecord] = {}
        self._lock = threading.Lock()
        self._event_queues: dict[str, _queue.Queue] = {}

    def create(self, record: JobRecord) -> None:
        with self._lock:
            self._jobs[record.job_id] = record
            self._event_queues[record.job_id] = _queue.Queue()

    def push_event(self, job_id: str, event: dict[str, Any]) -> None:
        """Push a progress event onto the job's SSE queue."""
        q = self._event_queues.get(job_id)
        if q is not None:
            q.put(event)

    def get_event_queue(self, job_id: str) -> "_queue.Queue | None":
        return self._event_queues.get(job_id)

    def update_status(self, job_id: str, status: str) -> None:
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id].status = status

    def update_from_result(
        self,
        job_id: str,
        result: RemediationResult,
        completed_at: str | None = None,
        processing_seconds: float | None = None,
    ) -> None:
        with self._lock:
            rec = self._jobs.get(job_id)
            if rec is None:
                return
            rec.status = result.status
            rec.page_count = result.page_count
            rec.compliance_score = result.compliance_score
            rec.compliance_grade = result.compliance_grade
            rec.remediated_pdf_uri = result.remediated_pdf_uri
            rec.tagged_pdf_uri = result.tagged_pdf_uri
            rec.html_uri = result.html_uri
            rec.markdown_uri = result.markdown_uri
            rec.log_uri = result.log_uri
            rec.manual_review_items = list(result.manual_review_items)
            rec.external_api_calls = list(result.external_api_calls)
            rec.completed_at = completed_at
            rec.processing_seconds = processing_seconds
            rec.error = result.error

    def set_started_at(self, job_id: str, started_at: str) -> None:
        with self._lock:
            rec = self._jobs.get(job_id)
            if rec is not None:
                rec.started_at = started_at

    def set_failed(self, job_id: str, error: str) -> None:
        with self._lock:
            rec = self._jobs.get(job_id)
            if rec is not None:
                rec.status = "failed"
                rec.error = error

    def get(self, job_id: str) -> JobRecord | None:
        with self._lock:
            return self._jobs.get(job_id)
