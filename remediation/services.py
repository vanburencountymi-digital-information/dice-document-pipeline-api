from django.utils import timezone

from accounts.models import ServiceAccount
from remediation.models import Remediation


class RemediationService:
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
