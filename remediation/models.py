from collections.abc import Iterable
from uuid import uuid4

from django.db import models
from django.db.models.base import ModelBase

from remediation.adapters.verification.severity import SEVERITY_RANK, Severity


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
    # `settings.PIPELINE_VERSION` at creation time (ADR 0012) — the git tag this attempt's
    # pipeline logic actually ran under. Lets a retry decide whether a FAILED attempt is
    # worth re-running: see `PipelineConfig.retry_floor_version` and ADR 0014.
    pipeline_version = models.CharField(max_length=20, blank=True)

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
        FONT_REPAIR = "font_repair", "Font repair"
        FINALIZE_METADATA = "finalize_metadata", "Finalize metadata"
        LINK_TAG = "link_tag", "Link tag"
        ALT_TEXT = "alt_text", "Alt text"
        SCORING = "scoring", "Scoring"
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


class VerificationResult(models.Model):
    """Persisted veraPDF verdict for one remediation/step (add_confidence_scoring)."""

    remediation = models.ForeignKey(
        Remediation, on_delete=models.CASCADE, related_name="verification_results"
    )
    step = models.CharField(max_length=20, choices=RemediationArtifact.Step.choices)
    is_compliant = models.BooleanField()
    # The exact veraPDF version that produced this result (from the report's own
    # `buildInformation` block, not hardcoded) — kept per-row since `severity.py`'s
    # classification table is reviewed against one version at a time
    # (`BUILT_AGAINST_VERAPDF_VERSION`); this is how an old row stays traceable to what
    # actually ran even after that table moves on to a newer version.
    verapdf_version = models.CharField(max_length=20, blank=True)
    # List of `remediation.adapters.base.FailedRuleDict` dicts (simplify_vera_printouts) —
    # replaces the raw veraPDF XML report that used to be dumped verbatim into
    # `Remediation.error`. `severity` inside each dict is a `Severity.value` string (JSON has
    # no enum type); every place that produces or reads it goes through `Severity`.
    failed_rules = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["remediation", "step"], name="one_verification_result_per_step"
            )
        ]

    def __str__(self) -> str:
        return f"{self.remediation_id}: {self.step} ({self.is_compliant})"

    @property
    def worst_severity(self) -> Severity | None:
        """The worst `Severity` across `failed_rules`, or `None` if there are none (e.g. a
        compliant result). CRITICAL outranks MAJOR outranks MINOR outranks UNCLASSIFIED.
        """
        present = {rule["severity"] for rule in self.failed_rules}
        for severity in SEVERITY_RANK:
            if severity.value in present:
                return severity
        return None


class PipelineConfig(models.Model):
    """Singleton admin-editable config (ADR 0012) — always the single row `pk=1`, enforced by
    `save()` below and by `PipelineConfigAdmin.has_add_permission` (`remediation/admin.py`)
    refusing a second row once one exists.

    `retry_floor_version` gates ADR 0014's auto-retry: resubmitting a `FAILED` `Remediation`
    whose `pipeline_version` is strictly older than this triggers a fresh attempt. Left blank
    by default so nothing auto-retries until someone deliberately raises it in admin.
    """

    retry_floor_version = models.CharField(max_length=20, blank=True, default="")

    def __str__(self) -> str:
        return f"Pipeline config (retry floor: {self.retry_floor_version or 'none'})"

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        self.pk = 1
        super().save(
            force_insert=force_insert,
            force_update=force_update,
            using=using,
            update_fields=update_fields,
        )


class RemediationScore(models.Model):
    """Heuristic 0-100 compliance score, ported from ada-remediation-pipeline for
    comparison against veraPDF (add_confidence_scoring). One row per successfully
    scored Remediation; a scoring failure leaves no row here (see the SCORING
    RemediationArtifact instead).
    """

    class Grade(models.TextChoices):
        A = "A", "A"
        B = "B", "B"
        C = "C", "C"
        D = "D", "D"
        F = "F", "F"

    remediation = models.OneToOneField(Remediation, on_delete=models.CASCADE, related_name="score")
    score = models.PositiveSmallIntegerField()
    grade = models.CharField(max_length=1, choices=Grade.choices)
    manual_review_items = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.remediation_id}: {self.score} ({self.grade})"
