import hashlib
import logging
import os
from urllib.parse import urljoin

from django.conf import settings
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import UploadedFile
from django.urls import reverse
from django.utils import timezone
from packaging.version import InvalidVersion, Version

from accounts.models import ServiceAccount
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
from remediation.adapters.verification.severity import SEVERITY_RANK
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

    def construct_output_dir(self, remediation: Remediation) -> str:
        """This step's own working directory for one remediation attempt (ADR 0008):
        `remediations/<service_account_id>/<content_hash>/<remediation_id>/<step>`.

        Assumes FileSystemStorage — same assumption `VerificationService.run` already
        makes for `pdf_path`, called out there rather than repeated at every call site.
        """
        return default_storage.path(
            f"remediations/{remediation.service_account_id}/"
            f"{remediation.content_hash}/{remediation.id}/{self.step.value}"
        )

    def _mark_status(
        self, remediation: Remediation, status: RemediationArtifact.StepStatus, **fields: str
    ) -> RemediationArtifact:
        return RemediationArtifact.objects.create(
            remediation=remediation, step=self.step, status=status, **fields
        )

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

    def run(self, remediation: Remediation, *, pdf_uri: str) -> str:
        # default_storage.path() assumes FileSystemStorage — will need reworking once a
        # GCS backend is wired in (implementation_plan.md Backlog), since veraPDF needs a
        # real local file path, not a storage-abstracted name/URL.
        pdf_path = default_storage.path(pdf_uri)
        try:
            outcome = self.adapter.validate(pdf_path)
        except Exception as exc:
            LOGGER.exception("remediation %s: %s adapter failed", remediation.id, self.step)
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

    def run(self, remediation: Remediation, *, pdf_uri: str) -> str:
        pdf_path = default_storage.path(pdf_uri)
        output_dir = self.construct_output_dir(remediation)

        try:
            output_path = self.adapter.extract(pdf_path, output_dir=output_dir)
        except Exception as exc:
            LOGGER.exception("remediation %s: %s failed", remediation.id, self.step)
            self.mark_failed(remediation, str(exc))
            return pdf_uri

        output_uri = os.path.relpath(output_path, default_storage.path(""))
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

    def run(self, remediation: Remediation, *, pdf_uri: str) -> str:
        pdf_path = default_storage.path(pdf_uri)
        output_dir = self.construct_output_dir(remediation)

        try:
            output_path = self.adapter.repair(pdf_path, output_dir=output_dir)
        except Exception as exc:
            LOGGER.exception("remediation %s: %s failed", remediation.id, self.step)
            self.mark_failed(remediation, str(exc))
            return pdf_uri

        output_uri = os.path.relpath(output_path, default_storage.path(""))
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

    def run(self, remediation: Remediation, *, pdf_uri: str) -> str:
        pdf_path = default_storage.path(pdf_uri)
        output_dir = self.construct_output_dir(remediation)

        try:
            output_path = self.adapter.finalize(
                pdf_path,
                output_dir=output_dir,
                title=_derive_title(remediation),
                lang=settings.LANGUAGE_CODE,
            )
        except Exception as exc:
            LOGGER.exception("remediation %s: %s failed", remediation.id, self.step)
            self.mark_failed(remediation, str(exc))
            return pdf_uri

        output_uri = os.path.relpath(output_path, default_storage.path(""))
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

    def run(self, remediation: Remediation, *, pdf_uri: str) -> str:
        pdf_path = default_storage.path(pdf_uri)
        output_dir = self.construct_output_dir(remediation)

        try:
            output_path = self.adapter.repair(pdf_path, output_dir=output_dir)
        except Exception as exc:
            LOGGER.exception("remediation %s: %s failed", remediation.id, self.step)
            self.mark_failed(remediation, str(exc))
            return pdf_uri

        output_uri = os.path.relpath(output_path, default_storage.path(""))
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

    def run(self, remediation: Remediation, *, pdf_uri: str) -> str:
        pdf_path = default_storage.path(pdf_uri)
        output_dir = self.construct_output_dir(remediation)
        document_title = _derive_title(remediation)

        try:
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
                pdf_path, output_dir=output_dir, alt_by_ref=alt_by_ref
            )
        except Exception as exc:
            LOGGER.exception("remediation %s: %s failed", remediation.id, self.step)
            self.mark_failed(remediation, str(exc))
            return pdf_uri

        output_uri = os.path.relpath(output_path, default_storage.path(""))
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

    def run(self, remediation: Remediation, *, pdf_uri: str) -> str:
        pdf_path = default_storage.path(pdf_uri)
        try:
            result = self.adapter.score(pdf_path)
        except Exception as exc:
            LOGGER.exception("remediation %s: %s failed", remediation.id, self.step)
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
