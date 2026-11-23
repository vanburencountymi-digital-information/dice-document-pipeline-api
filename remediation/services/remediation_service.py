import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urljoin

from django.conf import settings
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import UploadedFile
from django.db import IntegrityError, transaction
from django.db.models import Count, Min
from django.urls import reverse
from django.utils import timezone
from packaging.version import InvalidVersion, Version

from accounts.models import ServiceAccount
from common.db import retry_on_lost_connection
from remediation.models import (
    UNFINISHED_STATUSES,
    JobStatus,
    PipelineConfig,
    Remediation,
    RemediationCallback,
)

LOGGER = logging.getLogger(__name__)


def _parse_version(value: str) -> Version:
    """Best-effort semver parse for retry-eligibility comparisons (ADR 0014) — a blank or
    otherwise unparseable `pipeline_version` (e.g. a pre-ADR-0012 row) is treated as older
    than anything real, not as an error.
    """
    try:
        return Version(value) if value else Version("0.0.0")
    except InvalidVersion:
        return Version("0.0.0")


def build_download_url(remediation: Remediation) -> str:
    """Absolute download URL for a `Remediation`'s `final_output_uri` (ADR 0015) — one call
    site shared by `RemediationSerializer` (a polling caller) and `send_webhook_notification`
    (a webhook payload) so the two can't drift into building it differently.
    """
    return urljoin(
        settings.PUBLIC_BASE_URL, reverse("document-download", args=[remediation.content_hash])
    )


@dataclass(frozen=True)
class SubmissionUsage:
    """An account's recent attempts: how many (`count`), and when the earliest of them was
    made (`oldest`, None if there are none)."""

    count: int
    oldest: datetime | None


class RemediationService:
    def _hash_file(self, uploaded_file: UploadedFile) -> str:
        hasher = hashlib.sha256()
        for chunk in uploaded_file.chunks():
            hasher.update(chunk)
        uploaded_file.seek(0)
        return hasher.hexdigest()

    def get(self, remediation_id: str) -> Remediation:
        return Remediation.objects.get(pk=remediation_id)

    def get_or_create_from_upload(
        self,
        service_account: ServiceAccount,
        uploaded_file: UploadedFile,
        force: bool = False,
        before_create: Callable[[], None] | None = None,
    ) -> tuple[Remediation, bool]:
        """Submits an uploaded PDF for remediation, deduplicating by content hash.

        If `force` is `True`, always creates a new attempt, regardless of previous
        remediations status (unless one is still unfinished: that one is returned).

        If existing attempt is `Failed` and pipeline version from attempt is older than
        floor, auto-retries with the newer version of the pipeline.

        Takes: the account, the uploaded PDF, `force`, and `before_create`. `before_create` is
        called only when a new attempt is about to be made (never when an existing one is
        reused), inside this method's transaction. The submit view passes the account's limit
        check here, so a resubmit of a file already accepted is never turned away for being
        over the limit. If it raises, nothing is made.

        Returns `(remediation, created)` — `created` is `False` when an existing attempt
        is reused as-is.
        """
        # RemediationUploadSerializer.validate_file has already checked for non-empty pdf name
        original_filename = uploaded_file.name
        assert original_filename, "uploaded_file.name must be set"
        content_hash = self._hash_file(uploaded_file)
        # One transaction for the whole decision, so `before_create` (which may lock the
        # account's row to count its submissions) and the job it allows are made together.
        with transaction.atomic():
            reusable = self._reusable_attempt(service_account, content_hash, force=force)
            if reusable is not None:
                return reusable, False

            if before_create is not None:
                before_create()
                # The hook may have waited for the account's lock while another request made
                # this very attempt, so look again before making a second one.
                reusable = self._reusable_attempt(service_account, content_hash, force=force)
                if reusable is not None:
                    return reusable, False

            original_path = self._store_original(
                service_account, content_hash, original_filename, uploaded_file
            )
            return self._create_or_reuse_unfinished(
                service_account, content_hash, original_path, original_filename
            )

    def _reusable_attempt(
        self, service_account: ServiceAccount, content_hash: str, *, force: bool
    ) -> Remediation | None:
        """Takes: the account, the document's hash and `force`. Returns: the latest attempt if it
        should be returned as is, or None if a new attempt is needed."""
        existing = self.latest_for_document(service_account, content_hash)
        if existing is None:
            return None
        # Even `force` can't start a second attempt while one is still waiting or running (the
        # database refuses it anyway). Say so here, before the limit check and the storage write.
        if existing.status in UNFINISHED_STATUSES:
            return existing
        return None if self._should_retry(existing, force=force) else existing

    def _store_original(
        self,
        service_account: ServiceAccount,
        content_hash: str,
        original_filename: str,
        uploaded_file: UploadedFile,
    ) -> str:
        """Saves the uploaded original unless it is already stored.

        Takes: the account, the document's hash, the upload's filename and the upload.
        Returns: the storage path.
        """
        # Keyed by (service_account, content_hash) rather than upload-time filename —
        # deterministic, so the same original document never occupies more than one path even
        # on retry (ADR 0014): `default_storage.exists()` below just finds it already there
        # and skips re-saving the original.
        original_path = f"remediations/{service_account.id}/{content_hash}/{original_filename}"
        if not default_storage.exists(original_path):
            default_storage.save(original_path, uploaded_file)
        return original_path

    def _create_or_reuse_unfinished(
        self,
        service_account: ServiceAccount,
        content_hash: str,
        original_path: str,
        original_filename: str,
    ) -> tuple[Remediation, bool]:
        """Makes a new attempt, or returns the unfinished one that is already there.

        Takes: the account, the document's hash, where the original is stored and its filename.
        Returns: `(remediation, created)`.
        """
        try:
            # Own savepoint, so a clash doesn't spoil the caller's transaction.
            with transaction.atomic():
                remediation = self.create(
                    service_account,
                    source_pdf_uri=original_path,
                    content_hash=content_hash,
                    original_filename=original_filename,
                )
        except IntegrityError:
            # Another request made an unfinished attempt for this document after we looked
            # (or `force` was used on one still running): return that one, make no new job.
            current = self.latest_for_document(service_account, content_hash)
            if current is None or current.status not in UNFINISHED_STATUSES:
                raise
            return current, False
        return remediation, True

    def _is_stuck(self, remediation: Remediation) -> bool:
        """Takes: an attempt. Returns: True if it is unfinished and has made no progress for
        too long (`STUCK_QUEUED_MINUTES` if queued, `STUCK_RUNNING_MINUTES` if running), i.e. its
        task was probably lost (the process died before it was queued, or a worker died).
        Being queued again (`last_enqueued_at`) counts as progress.
        """
        if remediation.status == JobStatus.QUEUED:
            minutes = settings.STUCK_QUEUED_MINUTES
            last_progress = remediation.created_at
        elif remediation.status == JobStatus.RUNNING:
            minutes = settings.STUCK_RUNNING_MINUTES
            last_progress = remediation.started_at or remediation.created_at
        else:
            return False
        if remediation.last_enqueued_at is not None:
            last_progress = max(last_progress, remediation.last_enqueued_at)
        return timezone.now() - last_progress > timedelta(minutes=minutes)

    def claim_enqueue(self, remediation: Remediation, created: bool) -> bool:
        """Claims the right to queue this attempt's task now. Calling it uses up the claim, so
        call it once per request, and queue the task if it returns True.

        Takes: the attempt `get_or_create_from_upload` returned, and whether it was just made.
        Returns: True for a new attempt, and for a reused one that looks stuck (see
        `_is_stuck`). For a stuck one only a single caller gets True: the claim below is one
        conditional update, so two resubmits at the same moment can't both queue it, and a
        later resubmit doesn't queue it again until another full wait has passed.
        """
        if created:
            return True
        if not self._is_stuck(remediation):
            return False
        now = timezone.now()
        claimed = Remediation.objects.filter(
            pk=remediation.pk,
            # Skips a job that finished since we read it, so we never queue a finished one.
            status__in=UNFINISHED_STATUSES,
            # Matches only if nobody has claimed it since we read it: the compare in
            # compare-and-set. `None` becomes IS NULL, so the first claim works too.
            last_enqueued_at=remediation.last_enqueued_at,
        ).update(last_enqueued_at=now)
        if claimed:
            remediation.last_enqueued_at = now
        return bool(claimed)

    def recent_submissions(
        self, service_account: ServiceAccount, window_seconds: int, *, lock: bool = False
    ) -> SubmissionUsage:
        """Counts the account's attempts made in the last `window_seconds` (ADR 0031).

        Takes: the account, the window length in seconds, and `lock`. If `lock` is true, the
        account's row is locked until the caller's transaction ends, so concurrent submits
        for the account take turns. Must then be called inside `transaction.atomic()`.
        Returns: how many attempts, and when the oldest was made (None if there are none).
        """
        if lock:
            ServiceAccount.objects.select_for_update().get(pk=service_account.pk)
        since = timezone.now() - timedelta(seconds=window_seconds)
        usage = Remediation.objects.filter(
            service_account=service_account, created_at__gte=since
        ).aggregate(count=Count("id"), oldest=Min("created_at"))
        return SubmissionUsage(count=usage["count"], oldest=usage["oldest"])

    def _should_retry(self, existing: Remediation, *, force: bool) -> bool:
        if force:
            return True
        if existing.status not in (
            JobStatus.NONCOMPLIANT,
            JobStatus.ERROR,
        ):
            return False
        floor = self._retry_floor_version()
        if not floor:
            return False
        return _parse_version(existing.pipeline_version) < _parse_version(floor)

    def _retry_floor_version(self) -> str:
        config = PipelineConfig.objects.first()
        return config.retry_floor_version if config else ""

    def latest_for_document(
        self, service_account: ServiceAccount, content_hash: str
    ) -> Remediation | None:
        return (
            Remediation.objects.filter(service_account=service_account, content_hash=content_hash)
            .order_by("-created_at")
            .first()
        )

    def create(
        self,
        service_account: ServiceAccount,
        *,
        source_pdf_uri: str,
        content_hash: str,
        original_filename: str = "",
    ) -> Remediation:
        return Remediation.objects.create(
            service_account=service_account,
            source_pdf_uri=source_pdf_uri,
            content_hash=content_hash,
            original_filename=original_filename,
            pipeline_version=settings.PIPELINE_VERSION,
        )

    def claim_for_running(self, remediation: Remediation) -> bool:
        """Takes: an attempt. Marks it running and records when it started.
        Returns: False (changing nothing) if the attempt is already finished.

        The "not finished" check and the write are one database statement, so a redelivered
        task can't flip an attempt back to running after another worker finishes it. A
        RUNNING attempt is still claimable, because a worker that crashed mid-run leaves
        that status behind and its redelivery has to be able to pick the job up (ADR 0019).
        """
        started_at = timezone.now()
        claimed = (
            Remediation.objects.filter(pk=remediation.pk)
            .exclude(status__in=Remediation.FINISHED_STATUSES)
            .update(status=JobStatus.RUNNING, started_at=started_at)
        )
        if not claimed:
            return False
        remediation.status = JobStatus.RUNNING
        remediation.started_at = started_at
        return True

    def mark_compliant(self, remediation: Remediation, final_output_uri: str) -> None:
        self._mark_finished(remediation, JobStatus.COMPLIANT, final_output_uri)

    def mark_skipped(self, remediation: Remediation, final_output_uri: str) -> None:
        """Pipeline ran to the end but postcheck is disabled, so there's no verdict."""
        self._mark_finished(remediation, JobStatus.SKIPPED, final_output_uri)

    def mark_noncompliant(
        self, remediation: Remediation, error: str, final_output_uri: str
    ) -> None:
        self._mark_finished(remediation, JobStatus.NONCOMPLIANT, final_output_uri, error=error)

    def mark_error(self, remediation: Remediation, error: str, final_output_uri: str) -> None:
        self._mark_finished(remediation, JobStatus.ERROR, final_output_uri, error=error)

    @retry_on_lost_connection
    def _mark_finished(
        self,
        remediation: Remediation,
        status: JobStatus,
        final_output_uri: str,
        *,
        error: str | None = None,
    ) -> None:
        remediation.status = status
        remediation.completed_at = timezone.now()
        remediation.final_output_uri = final_output_uri
        update_fields = ["status", "completed_at", "final_output_uri"]
        if error is not None:
            remediation.error = error
            update_fields.append("error")
        remediation.save(update_fields=update_fields)

    def register_callback(
        self, remediation: Remediation, callback_url: str
    ) -> tuple[RemediationCallback, bool]:
        """Registers a webhook subscriber for this attempt (ADR 0015). Idempotent — a caller
        registering the same `(remediation, callback_url)` pair twice (e.g. a retried
        request) reuses the existing row rather than erroring or duplicating delivery.

        Returns `(callback, created)`.
        """
        return RemediationCallback.objects.get_or_create(
            remediation=remediation, callback_url=callback_url
        )
