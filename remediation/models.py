from uuid import uuid4

from django.db import models


class Remediation(models.Model):
    class JobStatus(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        COMPLETE = "complete", "Complete"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    service_account = models.ForeignKey(
        "accounts.ServiceAccount", on_delete=models.PROTECT, related_name="remediation_jobs"
    )
    status = models.CharField(max_length=20, choices=JobStatus.choices, default=JobStatus.QUEUED)

    source_pdf_uri = models.CharField(max_length=500)
    error = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status"])]

    def __str__(self) -> str:
        return f"{self.service_account}: {self.source_pdf_uri} ({self.status})"

    @property
    def processing_seconds(self) -> float | None:
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None
