import hashlib
import os

from django.conf import settings
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import UploadedFile
from django.utils import timezone

from accounts.models import ServiceAccount
from remediation.adapters.alt_text.claude_vision import ClaudeVisionClient
from remediation.adapters.alt_text.pike_pdf import PikePdfAdapter as AltTextPikePdfAdapter
from remediation.adapters.base import (
    AdapterError,
    AltTextAdapter,
    AltTextClient,
    FontRepairAdapter,
    LinkAdapter,
    MetadataAdapter,
    OCRAdapter,
    ScoringAdapter,
    VerificationAdapter,
)
from remediation.adapters.font_repair.pike_pdf import PikePdfAdapter as FontRepairPikePdfAdapter
from remediation.adapters.link.pike_pdf import PikePdfAdapter as LinkPikePdfAdapter
from remediation.adapters.metadata.pike_pdf import PikePdfAdapter
from remediation.adapters.ocr.open_data_loader import OpenDataLoaderAdapter
from remediation.adapters.scoring.pike_pdf import PikePdfAdapter as ScoringPikePdfAdapter
from remediation.adapters.verification.vera_pdf import VeraPDFAdapter
from remediation.models import (
    Remediation,
    RemediationArtifact,
    RemediationScore,
    VerificationResult,
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
        self, service_account: ServiceAccount, uploaded_file: UploadedFile
    ) -> tuple[Remediation, bool]:
        """Submits an uploaded PDF for remediation, deduplicating by content hash.

        Returns `(remediation, created)` — `created` is `False` whenever *any* attempt
        already exists for this document, including a `FAILED` one: resubmitting the same
        file never auto-retriggers a job, it just reports that attempt's current state.
        There is deliberately no retry mechanism yet (a document stuck on `FAILED` stays
        `FAILED` until one exists) — see ADR 0009.
        """
        content_hash = self._hash_file(uploaded_file)
        existing = self.latest_for_document(service_account, content_hash)
        if existing is not None:
            return existing, False

        # RemediationUploadSerializer.validate_file already guarantees a non-empty ".pdf"
        # name by the time an upload reaches here — `or ""` is only to satisfy the type
        # checker against UploadedFile.name's `str | None` stub, not a real fallback path.
        original_filename = uploaded_file.name or ""

        # Keyed by (service_account, content_hash) rather than upload-time filename —
        # deterministic, so the same document never occupies more than one path even if
        # something upstream (e.g. a future retry mechanism) re-saves it.
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
        )

    def mark_running(self, remediation: Remediation) -> None:
        remediation.status = Remediation.JobStatus.RUNNING
        remediation.started_at = timezone.now()
        remediation.save(update_fields=["status", "started_at"])

    def mark_complete(self, remediation: Remediation) -> None:
        remediation.status = Remediation.JobStatus.COMPLETE
        remediation.completed_at = timezone.now()
        remediation.save(update_fields=["status", "completed_at"])

    def mark_failed(self, remediation: Remediation, error: str) -> None:
        remediation.status = Remediation.JobStatus.FAILED
        remediation.error = error
        remediation.completed_at = timezone.now()
        remediation.save(update_fields=["status", "error", "completed_at"])


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


class AlreadyCompliant(Exception):
    """Not an error — raised by `PrecheckService` when the document already passes
    verification, signalling the pipeline to stop early and mark the job complete
    (ADR 0003)."""


class NotCompliant(Exception):
    """Not an error — raised by `PostCheckService` when the document still isn't compliant
    after remediation, signalling the job should be marked failed (ADR 0003)."""


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
            is_compliant, report = self.adapter.validate(pdf_path)
        except AdapterError as exc:
            self.mark_failed(remediation, str(exc))
            raise

        VerificationResult.objects.create(
            remediation=remediation, step=self.step, is_compliant=is_compliant
        )
        self.mark_completed(remediation, output_uri=pdf_uri)
        self.handle_result(is_compliant, report)
        return pdf_uri

    def handle_result(self, is_compliant: bool, report: str) -> None:
        raise NotImplementedError


class PrecheckService(VerificationService):
    step = RemediationArtifact.Step.PRECHECK

    def handle_result(self, is_compliant: bool, report: str) -> None:
        if is_compliant:
            raise AlreadyCompliant


class PostCheckService(VerificationService):
    step = RemediationArtifact.Step.POSTCHECK

    def handle_result(self, is_compliant: bool, report: str) -> None:
        if not is_compliant:
            raise NotCompliant(report)


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
        except AdapterError as exc:
            self.mark_failed(remediation, str(exc))
            raise

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
        except AdapterError as exc:
            self.mark_failed(remediation, str(exc))
            raise

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
        except AdapterError as exc:
            self.mark_failed(remediation, str(exc))
            raise

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
        except AdapterError as exc:
            self.mark_failed(remediation, str(exc))
            raise

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
        except AdapterError as exc:
            self.mark_failed(remediation, str(exc))
            raise

        output_uri = os.path.relpath(output_path, default_storage.path(""))
        self.mark_completed(remediation, output_uri=output_uri)
        return output_uri


class ScoringService(ArtifactService):
    """Non-blocking heuristic scoring (add_confidence_scoring experiment). Unlike every
    other step, `run()` never re-raises — a failure here must never block postcheck.
    """

    step = RemediationArtifact.Step.SCORING

    def __init__(self, adapter: ScoringAdapter | None = None) -> None:
        self.adapter = adapter or ScoringPikePdfAdapter()

    def run(self, remediation: Remediation, *, pdf_uri: str) -> str:
        pdf_path = default_storage.path(pdf_uri)
        try:
            result = self.adapter.score(pdf_path)
        except AdapterError as exc:
            self.mark_failed(remediation, str(exc))
            return pdf_uri
        except Exception as exc:
            # Belt-and-braces: non-blocking must hold even for a non-AdapterError bug.
            self.mark_failed(remediation, f"unexpected scoring error: {exc}")
            return pdf_uri

        RemediationScore.objects.create(
            remediation=remediation,
            score=result.score,
            grade=result.grade,
            manual_review_items=result.manual_review_items,
        )
        self.mark_completed(remediation, output_uri=pdf_uri)
        return pdf_uri
