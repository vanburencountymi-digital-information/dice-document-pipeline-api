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
    content_hash = models.CharField(max_length=64)  # sha256 hex digest
    original_filename = models.CharField(max_length=255, default="", blank=True)
    error = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["service_account", "content_hash"])]

    def __str__(self) -> str:
        return f"{self.service_account}: {self.source_pdf_uri} ({self.status})"

    @property
    def processing_seconds(self) -> float | None:
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None


class RemediationArtifact(models.Model):
    """Per-step outcome + output lineage for one `Remediation` attempt.

    One row per step actually reached (ADR 0003) — `Remediation.source_pdf_uri` stays the
    single immutable input; each step's `output_uri` is the file it handed to the next step.
    """

    class Step(models.TextChoices):
        PRECHECK = "precheck", "Precheck"
        OCR = "ocr", "OCR"
        FINALIZE_METADATA = "finalize_metadata", "Finalize metadata"
        ALT_TEXT = "alt_text", "Alt text"
        LINK_TAG = "link_tag", "Link tag"
        POSTCHECK = "postcheck", "Postcheck"

    class StepStatus(models.TextChoices):
        COMPLETED = "completed", "Completed"
        SKIPPED = "skipped", "Skipped"
        FAILED = "failed", "Failed"

    remediation = models.ForeignKey(Remediation, on_delete=models.CASCADE, related_name="artifacts")
    step = models.CharField(max_length=20, choices=Step.choices)
    status = models.CharField(max_length=20, choices=StepStatus.choices)
    output_uri = models.CharField(max_length=500, blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(fields=["remediation", "step"], name="one_artifact_per_step")
        ]

    def __str__(self) -> str:
        return f"{self.remediation_id}: {self.step} ({self.status})"
