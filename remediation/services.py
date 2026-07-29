import hashlib

from django.core.files.storage import default_storage
from django.core.files.uploadedfile import UploadedFile
from django.utils import timezone

from accounts.models import ServiceAccount
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

    Subclasses set `step` to the `RemediationArtifact.Step` they represent.
    """

    step: RemediationArtifact.Step

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
