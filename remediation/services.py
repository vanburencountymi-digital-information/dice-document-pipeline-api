import hashlib
import os

from django.conf import settings
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import UploadedFile
from django.utils import timezone

from accounts.models import ServiceAccount
from remediation.adapters.base import AdapterError, OCRAdapter, VerificationAdapter
from remediation.adapters.ocr.open_data_loader import OpenDataLoaderAdapter
from remediation.adapters.verification.vera_pdf import VeraPDFAdapter
from remediation.models import Remediation, RemediationArtifact


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

        Returns `(remediation, created)` — `created` is `False` when a reusable
        (non-`FAILED`) attempt already exists for this document, in which case
        storage is never touched.
        """
        content_hash = self._hash_file(uploaded_file)
        existing = self.find_existing(service_account, content_hash)
        if existing is not None:
            return existing, False

        name = default_storage.save(f"remediations/{uploaded_file.name}", uploaded_file)
        remediation = self.create(service_account, source_pdf_uri=name, content_hash=content_hash)
        return remediation, True

    def find_existing(
        self, service_account: ServiceAccount, content_hash: str
    ) -> Remediation | None:
        return (
            Remediation.objects.filter(service_account=service_account, content_hash=content_hash)
            .exclude(status=Remediation.JobStatus.FAILED)
            .order_by("-created_at")
            .first()
        )

    def latest_for_document(
        self, service_account: ServiceAccount, content_hash: str
    ) -> Remediation | None:
        return (
            Remediation.objects.filter(service_account=service_account, content_hash=content_hash)
            .order_by("-created_at")
            .first()
        )

    def create(
        self, service_account: ServiceAccount, *, source_pdf_uri: str, content_hash: str
    ) -> Remediation:
        return Remediation.objects.create(
            service_account=service_account,
            source_pdf_uri=source_pdf_uri,
            content_hash=content_hash,
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
            is_compliant, _report = self.adapter.validate(pdf_path)
        except AdapterError as exc:
            self.mark_failed(remediation, str(exc))
            raise

        self.mark_completed(remediation, output_uri=pdf_uri)
        self.handle_result(is_compliant)
        return pdf_uri

    def handle_result(self, is_compliant: bool) -> None:
        raise NotImplementedError


class PrecheckService(VerificationService):
    step = RemediationArtifact.Step.PRECHECK

    def handle_result(self, is_compliant: bool) -> None:
        if is_compliant:
            raise AlreadyCompliant


class PostCheckService(VerificationService):
    step = RemediationArtifact.Step.POSTCHECK

    def handle_result(self, is_compliant: bool) -> None:
        if not is_compliant:
            raise NotCompliant


class OCRService(ArtifactService):
    """Stage 2 of ADR 0003's pipeline: OCR + auto-tagging in one pass.
    Produces tagged structure but requires more fixup.
    """

    step = RemediationArtifact.Step.OCR

    def __init__(self, adapter: OCRAdapter | None = None) -> None:
        self.adapter = adapter or OpenDataLoaderAdapter(
            hybrid_url=settings.OPENDATALOADER_HYBRID_URL
        )

    def run(self, remediation: Remediation, *, pdf_uri: str) -> str:
        # Same FileSystemStorage assumption as VerificationService.run — see its comment.
        pdf_path = default_storage.path(pdf_uri)
        output_dir = default_storage.path(f"remediations/{remediation.id}/ocr")
        try:
            output_path = self.adapter.tag(pdf_path, output_dir=output_dir)
        except AdapterError as exc:
            self.mark_failed(remediation, str(exc))
            raise

        output_uri = os.path.relpath(output_path, default_storage.path(""))
        self.mark_completed(remediation, output_uri=output_uri)
        return output_uri
