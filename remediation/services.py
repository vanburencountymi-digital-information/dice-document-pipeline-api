import hashlib
import logging
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from urllib.parse import urljoin

from django.conf import settings
from django.core.files.base import File
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import UploadedFile
from django.urls import reverse
from django.utils import timezone
from django_tasks_db.models import DBTaskResult
from packaging.version import InvalidVersion, Version

from accounts.models import ServiceAccount
from common.error_logging_client import ErrorLoggingClient
from remediation.adapters.alt_text.claude_vision import ClaudeVisionClient
from remediation.adapters.alt_text.pike_pdf import (
    PikePdfAdapter as AltTextPikePdfAdapter,
)
from remediation.adapters.base import (
    AltTextAdapter,
    AltTextClient,
    FailedRule,
    FailedRuleDict,
    FontRepairAdapter,
    LinkAdapter,
    MetadataAdapter,
    OCRAdapter,
    ScoringAdapter,
    VerificationAdapter,
)
from remediation.adapters.font_repair.pike_pdf import (
    PikePdfAdapter as FontRepairPikePdfAdapter,
)
from remediation.adapters.link.pike_pdf import PikePdfAdapter as LinkPikePdfAdapter
from remediation.adapters.metadata.pike_pdf import PikePdfAdapter
from remediation.adapters.ocr.open_data_loader import OpenDataLoaderAdapter
from remediation.adapters.scoring.pike_pdf import (
    PikePdfAdapter as ScoringPikePdfAdapter,
)
from remediation.adapters.verification.severity import SEVERITY_RANK, Severity
from remediation.adapters.verification.vera_pdf import VeraPDFAdapter
from remediation.models import (
    PipelineConfig,
    Remediation,
    RemediationArtifact,
    RemediationCallback,
    RemediationScore,
    VerificationResult,
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
        self, service_account: ServiceAccount, uploaded_file: UploadedFile, force: bool = False
    ) -> tuple[Remediation, bool]:
        """Submits an uploaded PDF for remediation, deduplicating by content hash.

        If `force` is `True`, always creates a new attempt, regardless of previous
        remediations status.

        If existing attempt is `Failed` and pipeline version from attempt is older than
        floor, auto-retries with the newer version of the pipeline.

        Returns `(remediation, created)` — `created` is `False` when an existing attempt
        is reused as-is.
        """
        content_hash = self._hash_file(uploaded_file)
        existing = self.latest_for_document(service_account, content_hash)
        if existing is not None and not self._should_retry(existing, force=force):
            return existing, False

        # RemediationUploadSerializer.validate_file has already checked for non-empty pdf name
        original_filename = uploaded_file.name
        assert original_filename, "uploaded_file.name must be set"

        # Keyed by (service_account, content_hash) rather than upload-time filename —
        # deterministic, so the same original document never occupies more than one path even on
        # retry (ADR 0014): `default_storage.exists()` below just finds it already there
        # and skips re-saving the original.
        original_path = f"remediations/{service_account.id}/{content_hash}/{original_filename}"
        if not default_storage.exists(original_path):
            default_storage.save(original_path, uploaded_file)
        remediation = self.create(
            service_account,
            source_pdf_uri=original_path,
            content_hash=content_hash,
            original_filename=original_filename,
        )
        return remediation, True

    def _should_retry(self, existing: Remediation, *, force: bool) -> bool:
        if force:
            return True
        if existing.status != Remediation.JobStatus.FAILED:
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

    def mark_running(self, remediation: Remediation) -> None:
        remediation.status = Remediation.JobStatus.RUNNING
        remediation.started_at = timezone.now()
        remediation.save(update_fields=["status", "started_at"])

    def mark_complete(self, remediation: Remediation, final_output_uri: str) -> None:
        remediation.status = Remediation.JobStatus.COMPLETE
        remediation.completed_at = timezone.now()
        remediation.final_output_uri = final_output_uri
        remediation.save(update_fields=["status", "completed_at", "final_output_uri"])

    def mark_failed(self, remediation: Remediation, error: str, final_output_uri: str) -> None:
        remediation.status = Remediation.JobStatus.FAILED
        remediation.error = error
        remediation.completed_at = timezone.now()
        remediation.final_output_uri = final_output_uri
        remediation.save(update_fields=["status", "error", "completed_at", "final_output_uri"])

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


class TaskQueueService:
    """Read-only view of the database task queue (ADR 0020), for `run_queued_tasks`
    (ADR 0023) to decide whether a scheduled run has anything to do.
    """

    def has_ready_tasks(self) -> bool:
        """True if any task is waiting and due now — the same `ready()` filter `db_worker`
        uses to pick work, so a deferred webhook retry that isn't due yet doesn't count.
        """
        return DBTaskResult.objects.ready().filter(backend_name="default").exists()


class ArtifactService:
    """Mixin for pipeline step services that records their outcome as a `RemediationArtifact`.

    Subclasses set `step` to the `RemediationArtifact.Step` they represent. Every step is
    gated by a `RUN_<STEP>` setting named after the step itself (default off), so gating a
    new step never needs its own registration — just add the setting.
    """

    step: RemediationArtifact.Step

    @property
    def setting_name(self) -> str:
        return f"RUN_{self.step.name}"

    def is_disabled(self) -> bool:
        return not getattr(settings, self.setting_name)

    def record_skip(self, remediation: Remediation) -> RemediationArtifact:
        return self.mark_skipped(remediation, f"{self.setting_name} is disabled")

    def report_exception(self, exc: Exception, remediation: Remediation) -> None:
        ErrorLoggingClient().report_exception(
            exc, tags={"step": self.step.value, "remediation_id": str(remediation.id)}
        )

    def construct_output_dir(self, remediation: Remediation) -> str:
        """This step's own storage-key prefix for one remediation attempt (ADR 0008):
        `remediations/<service_account_id>/<content_hash>/<remediation_id>/<step>/`.

        Pure string construction, not a real filesystem directory — `default_storage`
        isn't guaranteed to have one (`S3Storage.path()` raises `NotImplementedError`).
        Combine with a filename and pass to `persist_output` as the destination URI.
        """
        return (
            f"remediations/{remediation.service_account_id}/"
            f"{remediation.content_hash}/{remediation.id}/{self.step.value}/"
        )

    @contextmanager
    def local_input_copy(self, pdf_uri: str) -> Iterator[str]:
        """Copies `pdf_uri` out of `default_storage` into a real local file, since local
        tools (veraPDF, pikepdf, PyMuPDF, OpenDataLoader) need an actual filesystem path,
        not a storage-abstracted name. `.open()` is a standard `Storage` API method every
        backend implements, unlike `.path()`. Cleans up the temp file on exit either way.
        """
        with tempfile.TemporaryDirectory() as tmp_dir:
            local_path = os.path.join(tmp_dir, os.path.basename(pdf_uri))
            with default_storage.open(pdf_uri, "rb") as remote_file, open(local_path, "wb") as f:
                f.write(remote_file.read())
            yield local_path

    def persist_output(self, local_path: str, dest_uri: str) -> str:
        """Uploads a local tool's output file to `default_storage` at `dest_uri` — the
        write-side counterpart to `local_input_copy`. Returns the actual name saved to.

        Deletes any existing object at `dest_uri` first: ADR 0008's paths are deterministic
        on purpose (the same remediation + step always lands at the same key). Retries create
        new remediation rows, so this only happens when a redelivered task re-runs a step that
        didn't complete the first time (ADR 0019).
        """
        if default_storage.exists(dest_uri):
            default_storage.delete(dest_uri)
        with open(local_path, "rb") as fh:
            return default_storage.save(dest_uri, File(fh))

    def completed_output_uri(self, remediation: Remediation) -> str | None:
        """This step's `output_uri` if it already completed for `remediation`, else `None`
        (ADR 0019). Steps check this first so a redelivered task never re-runs finished work.
        Only `COMPLETED` counts — a `FAILED` or `SKIPPED` step is allowed to run again.
        """
        return (
            remediation.artifacts.filter(
                step=self.step, status=RemediationArtifact.StepStatus.COMPLETED
            )
            .values_list("output_uri", flat=True)
            .first()
        )

    def run(self, remediation: Remediation, *, pdf_uri: str) -> str:
        """Runs this step, unless it already completed for `remediation` (ADR 0019) — then
        `resume_completed` stands in for it, so a redelivered task never redoes finished work.
        Subclasses put their actual work in `_run`.
        """
        existing_output_uri = self.completed_output_uri(remediation)
        if existing_output_uri is not None:
            return self.resume_completed(remediation, existing_output_uri)
        return self._run(remediation, pdf_uri=pdf_uri)

    def resume_completed(self, remediation: Remediation, output_uri: str) -> str:
        """What `run` returns for an already-completed step. Default: the step's previous
        output. `VerificationService` overrides this to replay its stored verdict.
        """
        return output_uri

    def _run(self, remediation: Remediation, *, pdf_uri: str) -> str:
        raise NotImplementedError

    def _mark_status(
        self, remediation: Remediation, status: RemediationArtifact.StepStatus, **fields: str
    ) -> RemediationArtifact:
        # Upsert, not create (ADR 0019): a redelivered task re-running a previously failed
        # step overwrites its row instead of tripping `one_artifact_per_step`. Blank defaults
        # so a stale `error` doesn't survive a later success (and vice versa).
        artifact, _ = RemediationArtifact.objects.update_or_create(
            remediation=remediation,
            step=self.step,
            defaults={"status": status, "output_uri": "", "error": "", **fields},
        )
        return artifact

    def mark_completed(self, remediation: Remediation, output_uri: str) -> RemediationArtifact:
        return self._mark_status(
            remediation, RemediationArtifact.StepStatus.COMPLETED, output_uri=output_uri
        )

    def mark_skipped(self, remediation: Remediation, reason: str) -> RemediationArtifact:
        return self._mark_status(remediation, RemediationArtifact.StepStatus.SKIPPED, error=reason)

    def mark_failed(self, remediation: Remediation, error: str) -> RemediationArtifact:
        return self._mark_status(remediation, RemediationArtifact.StepStatus.FAILED, error=error)


def _format_failure_summary(failed_rules: list[FailedRule]) -> str:
    """Short human-readable rendering of failed rules (simplify_vera_printouts), replacing
    the raw veraPDF XML report that used to be dumped straight into `Remediation.error` —
    worst severity first, so the most-blocking issue is always the first line under the
    header.
    """
    total_checks = sum(rule.failed_checks for rule in failed_rules)
    header = f"{len(failed_rules)} rules failed, {total_checks} checks"
    ordered = sorted(failed_rules, key=lambda rule: SEVERITY_RANK.index(rule.severity))
    lines = [
        f"  - {rule.severity.label.upper():<12} {rule.clause} "
        f"{rule.description} ({rule.failed_checks} checks)"
        for rule in ordered
    ]
    return "\n".join([header, *lines])


class AlreadyCompliant(Exception):
    """Not an error — raised by `PrecheckService` when the document already passes
    verification, signalling the pipeline to stop early and mark the job complete
    (ADR 0003)."""


class NotCompliant(Exception):
    """Not an error — raised by `PostCheckService` when the document still isn't compliant
    after remediation, signalling the job should be marked failed (ADR 0003).

    `str(exc)` is a short human-readable severity-ranked summary (simplify_vera_printouts) —
    never the raw veraPDF XML report. `message` overrides that summary for the case where
    there's no `failed_rules` to summarize at all — postcheck's own adapter crashed rather
    than producing a real verdict (ADR 0013's `handle_verification_error`) — so the job still
    reads as an honest "not confirmed compliant" instead of a misleading "0 rules failed".
    """

    def __init__(
        self, failed_rules: list[FailedRule] | None = None, *, message: str | None = None
    ) -> None:
        self.failed_rules = failed_rules or []
        super().__init__(message or _format_failure_summary(self.failed_rules))


class VerificationService(ArtifactService):
    """Shared machinery for stages 1 and 6 of ADR 0003's pipeline — precheck and postcheck.

    Runs a `VerificationAdapter` against a PDF, records the outcome, and returns `pdf_uri`
    unchanged (verification doesn't transform the document) so every pipeline step shares the
    same "URI in, URI out" shape. Not instantiated directly — see `PrecheckService`/
    `PostCheckService`, which each fix `step` and `handle_result`.
    """

    def __init__(self, adapter: VerificationAdapter | None = None) -> None:
        self.adapter = adapter or VeraPDFAdapter()

    def resume_completed(self, remediation: Remediation, output_uri: str) -> str:
        """Replays the stored verdict rather than just returning early (ADR 0019) —
        `handle_result` is what raises `AlreadyCompliant`/`NotCompliant`, so skipping it
        would let a redelivered task carry on past a decision the first run already made.
        """
        previous = VerificationResult.objects.get(remediation=remediation, step=self.step)
        self.handle_result(
            previous.is_compliant,
            [
                FailedRule(
                    clause=rule["clause"],
                    test_number=rule["test_number"],
                    description=rule["description"],
                    failed_checks=rule["failed_checks"],
                    severity=Severity(rule["severity"]),
                )
                for rule in previous.failed_rules
            ],
        )
        return output_uri

    def _run(self, remediation: Remediation, *, pdf_uri: str) -> str:
        try:
            with self.local_input_copy(pdf_uri) as pdf_path:
                outcome = self.adapter.validate(pdf_path)
        except Exception as exc:
            LOGGER.exception("remediation %s: %s adapter failed", remediation.id, self.step)
            self.report_exception(exc, remediation)
            self.mark_failed(remediation, str(exc))
            self.handle_verification_error(str(exc))
            return pdf_uri

        VerificationResult.objects.create(
            remediation=remediation,
            step=self.step,
            is_compliant=outcome.is_compliant,
            verapdf_version=outcome.verapdf_version,
            failed_rules=[
                FailedRuleDict(
                    clause=rule.clause,
                    test_number=rule.test_number,
                    description=rule.description,
                    failed_checks=rule.failed_checks,
                    severity=rule.severity.value,
                )
                for rule in outcome.failed_rules
            ],
        )
        self.mark_completed(remediation, output_uri=pdf_uri)
        self.handle_result(outcome.is_compliant, outcome.failed_rules)
        return pdf_uri

    def handle_result(self, is_compliant: bool, failed_rules: list[FailedRule]) -> None:
        raise NotImplementedError

    def handle_verification_error(self, error: str) -> None:
        """Called instead of `handle_result` when the adapter itself couldn't produce a
        verdict at all (ADR 0013) — e.g. veraPDF crashed, as opposed to running and finding
        the document non-compliant. Default: treat as inconclusive and let the pipeline
        continue — `PrecheckService` doesn't need this to mean "already compliant" any more
        than it needs a real non-compliant verdict to. `PostCheckService` overrides this,
        since there's no later check for it to fall back on.
        """


class PrecheckService(VerificationService):
    step = RemediationArtifact.Step.PRECHECK

    def handle_result(self, is_compliant: bool, failed_rules: list[FailedRule]) -> None:
        if is_compliant:
            raise AlreadyCompliant


class PostCheckService(VerificationService):
    step = RemediationArtifact.Step.POSTCHECK

    def handle_result(self, is_compliant: bool, failed_rules: list[FailedRule]) -> None:
        if not is_compliant:
            raise NotCompliant(failed_rules)

    def handle_verification_error(self, error: str) -> None:
        raise NotCompliant(message=f"postcheck could not run: {error}")


class OCRService(ArtifactService):
    """OCR + tagging in one call, via a patched fork of `opendataloader-pdf` (ADR 0010) that
    fixes a confirmed upstream bug where hybrid mode's OCR-recovered text was silently
    dropped from `tagged-pdf`/`pdf` output. Produces the tagged PDF directly.
    """

    step = RemediationArtifact.Step.OCR

    def __init__(self, adapter: OCRAdapter | None = None) -> None:
        self.adapter = adapter or OpenDataLoaderAdapter(
            hybrid_url=settings.OPENDATALOADER_HYBRID_URL
        )

    def _run(self, remediation: Remediation, *, pdf_uri: str) -> str:
        try:
            with (
                self.local_input_copy(pdf_uri) as pdf_path,
                tempfile.TemporaryDirectory() as local_output_dir,
            ):
                output_path = self.adapter.extract(pdf_path, output_dir=local_output_dir)
                dest_uri = (
                    f"{self.construct_output_dir(remediation)}{os.path.basename(output_path)}"
                )
                output_uri = self.persist_output(output_path, dest_uri)
        except Exception as exc:
            LOGGER.exception("remediation %s: %s failed", remediation.id, self.step)
            self.report_exception(exc, remediation)
            self.mark_failed(remediation, str(exc))
            return pdf_uri

        self.mark_completed(remediation, output_uri=output_uri)
        return output_uri


class FontRepairService(ArtifactService):
    """Recovers missing `/ToUnicode` mappings on embedded fonts whose character codes are
    shifted from true Unicode by a constant, per-font offset — see the font-repair plan for the
    full root-cause writeup. Doesn't touch tag structure or metadata; purely a font-dictionary
    repair, same footprint as `LinkService`.
    """

    step = RemediationArtifact.Step.FONT_REPAIR

    def __init__(self, adapter: FontRepairAdapter | None = None) -> None:
        self.adapter = adapter or FontRepairPikePdfAdapter()

    def _run(self, remediation: Remediation, *, pdf_uri: str) -> str:
        try:
            with (
                self.local_input_copy(pdf_uri) as pdf_path,
                tempfile.TemporaryDirectory() as local_output_dir,
            ):
                output_path = self.adapter.repair(pdf_path, output_dir=local_output_dir)
                dest_uri = (
                    f"{self.construct_output_dir(remediation)}{os.path.basename(output_path)}"
                )
                output_uri = self.persist_output(output_path, dest_uri)
        except Exception as exc:
            LOGGER.exception("remediation %s: %s failed", remediation.id, self.step)
            self.report_exception(exc, remediation)
            self.mark_failed(remediation, str(exc))
            return pdf_uri

        self.mark_completed(remediation, output_uri=output_uri)
        return output_uri


DEFAULT_TITLE = "Untitled document"


def _derive_title(remediation: Remediation) -> str:
    """Shared by `MetadataService` (dc:title) and `AltTextService` (Claude prompt context).

    original_filename is blank on old rows predating that field, and callers can still
    pass "" — fall back rather than deriving an empty title.
    """
    stem = os.path.splitext(remediation.original_filename)[0]
    return stem or DEFAULT_TITLE


class MetadataService(ArtifactService):
    """Normalizes accessibility metadata OpenDataLoader's tagging leaves incomplete —
    `MarkInfo`/`Lang`/title/tab-order (ADR 0003 stage 3), via pikepdf. Doesn't touch tag
    structure; see ADR 0010 for why this step is `finalize_metadata`, not `finalize_tags`.
    """

    step = RemediationArtifact.Step.FINALIZE_METADATA

    def __init__(self, adapter: MetadataAdapter | None = None) -> None:
        self.adapter = adapter or PikePdfAdapter()

    def _run(self, remediation: Remediation, *, pdf_uri: str) -> str:
        try:
            with (
                self.local_input_copy(pdf_uri) as pdf_path,
                tempfile.TemporaryDirectory() as local_output_dir,
            ):
                output_path = self.adapter.finalize(
                    pdf_path,
                    output_dir=local_output_dir,
                    title=_derive_title(remediation),
                    lang=settings.LANGUAGE_CODE,
                )
                dest_uri = (
                    f"{self.construct_output_dir(remediation)}{os.path.basename(output_path)}"
                )
                output_uri = self.persist_output(output_path, dest_uri)
        except Exception as exc:
            LOGGER.exception("remediation %s: %s failed", remediation.id, self.step)
            self.report_exception(exc, remediation)
            self.mark_failed(remediation, str(exc))
            return pdf_uri

        self.mark_completed(remediation, output_uri=output_uri)
        return output_uri


class LinkService(ArtifactService):
    """Gives every untagged `/Link` annotation a struct-tree presence (ADR 0003 stage 5,
    "Repair links"), via pikepdf. No title/lang derivation — this step only touches
    annotation/struct-tree wiring, not document metadata.
    """

    step = RemediationArtifact.Step.LINK_TAG

    def __init__(self, adapter: LinkAdapter | None = None) -> None:
        self.adapter = adapter or LinkPikePdfAdapter()

    def _run(self, remediation: Remediation, *, pdf_uri: str) -> str:
        try:
            with (
                self.local_input_copy(pdf_uri) as pdf_path,
                tempfile.TemporaryDirectory() as local_output_dir,
            ):
                output_path = self.adapter.repair(pdf_path, output_dir=local_output_dir)
                dest_uri = (
                    f"{self.construct_output_dir(remediation)}{os.path.basename(output_path)}"
                )
                output_uri = self.persist_output(output_path, dest_uri)
        except Exception as exc:
            LOGGER.exception("remediation %s: %s failed", remediation.id, self.step)
            self.report_exception(exc, remediation)
            self.mark_failed(remediation, str(exc))
            return pdf_uri

        self.mark_completed(remediation, output_uri=output_uri)
        return output_uri


class AltTextService(ArtifactService):
    """Generates WCAG-standard `/Alt` descriptions for untagged, non-decorative `<Figure>`
    elements via Claude Vision (ADR 0003 stage 6, "Enrich figures"; ADR 0005). Splits PDF-
    side work (`AltTextAdapter`) from the vision-API call (`AltTextClient`) rather than one
    class doing both — this is the only stage that needs both an outside-package and an
    outside-API integration.
    """

    step = RemediationArtifact.Step.ALT_TEXT

    def __init__(
        self, adapter: AltTextAdapter | None = None, client: AltTextClient | None = None
    ) -> None:
        self.adapter = adapter or AltTextPikePdfAdapter()
        self.client = client or ClaudeVisionClient(
            api_key=settings.ANTHROPIC_API_KEY, model=settings.CLAUDE_VISION_MODEL
        )

    def _run(self, remediation: Remediation, *, pdf_uri: str) -> str:
        document_title = _derive_title(remediation)

        try:
            with (
                self.local_input_copy(pdf_uri) as pdf_path,
                tempfile.TemporaryDirectory() as local_output_dir,
            ):
                candidates = self.adapter.collect_figures(pdf_path)
                alt_by_ref: dict[tuple[int, int], str] = {}
                for candidate in candidates:
                    if candidate.decorative:
                        alt_by_ref[candidate.ref] = ""
                        continue
                    alt_by_ref[candidate.ref] = self.client.describe(
                        candidate.image_bytes,
                        media_type=candidate.media_type,
                        document_title=document_title,
                        page_number=candidate.page_number,
                    )
                output_path = self.adapter.write_alt_text(
                    pdf_path, output_dir=local_output_dir, alt_by_ref=alt_by_ref
                )
                dest_uri = (
                    f"{self.construct_output_dir(remediation)}{os.path.basename(output_path)}"
                )
                output_uri = self.persist_output(output_path, dest_uri)
        except Exception as exc:
            LOGGER.exception("remediation %s: %s failed", remediation.id, self.step)
            self.report_exception(exc, remediation)
            self.mark_failed(remediation, str(exc))
            return pdf_uri

        self.mark_completed(remediation, output_uri=output_uri)
        return output_uri


class ScoringService(ArtifactService):
    """Heuristic scoring (add_confidence_scoring experiment) — non-blocking even before
    ADR 0013 made that the norm for every step, since a score is advisory, never a
    compliance determination.
    """

    step = RemediationArtifact.Step.SCORING

    def __init__(self, adapter: ScoringAdapter | None = None) -> None:
        self.adapter = adapter or ScoringPikePdfAdapter()

    def _run(self, remediation: Remediation, *, pdf_uri: str) -> str:
        try:
            with self.local_input_copy(pdf_uri) as pdf_path:
                result = self.adapter.score(pdf_path)
        except Exception as exc:
            LOGGER.exception("remediation %s: %s failed", remediation.id, self.step)
            self.report_exception(exc, remediation)
            self.mark_failed(remediation, str(exc))
            return pdf_uri

        RemediationScore.objects.create(
            remediation=remediation,
            score=result.score,
            grade=result.grade,
            manual_review_items=result.manual_review_items,
        )
        self.mark_completed(remediation, output_uri=pdf_uri)
        return pdf_uri
