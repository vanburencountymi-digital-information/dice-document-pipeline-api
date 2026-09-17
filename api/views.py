from django.contrib.auth.models import AnonymousUser
from django.core.files.storage import default_storage
from django.http import FileResponse, Http404
from knox.auth import TokenAuthentication
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import ServiceAccount
from remediation.models import Remediation
from remediation.serializers import RemediationSerializer, RemediationUploadSerializer
from remediation.services import RemediationService
from remediation.tasks import process_remediation, send_webhook_notification


class ServiceAccountRequiredMixin(APIView):
    """Token-authenticates a request and exposes the caller's `ServiceAccount`."""

    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]

    @property
    def service_account(self) -> ServiceAccount:
        # IsAuthenticated already guarantees this; narrows the type for mypy.
        assert not isinstance(self.request.user, AnonymousUser)
        return self.request.user.serviceaccount


class StatusView(ServiceAccountRequiredMixin):
    """Placeholder; checks if user token is valid."""

    def get(self, request: Request) -> Response:
        return Response({"status": "ok"})


class CreateRemediationView(ServiceAccountRequiredMixin):
    """
    Handles incoming files for remediation jobs.

    If the file (by content hash) is new for the service account, enqueues a new
    remediation job.

    Otherwise finds the most recent existing job for that file — unless `force=true` is
    passed, or the existing job is FAILED and old enough to auto-retry (ADR 0014), in
    which case a new attempt is enqueued instead.

    An optional `callback_url` registers a webhook subscription (ADR 0015) on whichever
    attempt the request resolved to, new or deduped — any number of callers can each
    register their own callback on the same attempt. If that attempt is already terminal
    (COMPLETE/FAILED) at submission time — i.e. subscribing after the fact — the
    notification is enqueued immediately rather than waiting for a pipeline run that isn't
    going to happen.

    Takes:
        a PDF document, and optionally `force` (bool, default false) and `callback_url`.

    Returns:
        Response with the serialized remediation job (id, document_id, original_filename,
        status, error, created_at, started_at, completed_at, verification_results,
        download_url).
    """

    def post(self, request: Request) -> Response:
        upload = RemediationUploadSerializer(data=request.data)
        upload.is_valid(raise_exception=True)

        service = RemediationService()
        remediation, created = service.get_or_create_from_upload(
            self.service_account,
            upload.validated_data["file"],
            force=upload.validated_data["force"],
        )

        callback_url = upload.validated_data["callback_url"]
        if callback_url:
            callback, callback_created = service.register_callback(remediation, callback_url)
            # If job is already completed, post to webhook now.
            if callback_created and remediation.status in (
                Remediation.JobStatus.COMPLETE,
                Remediation.JobStatus.FAILED,
            ):
                send_webhook_notification.enqueue(str(callback.id))

        if created:
            process_remediation.enqueue(str(remediation.id))
            remediation.refresh_from_db()
        response_status = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        return Response(RemediationSerializer(remediation).data, status=response_status)


class DocumentStatusView(ServiceAccountRequiredMixin):
    """
    Looks up and returns the status of a document's most recent remediation attempt.

    Takes:
        content_hash: the SHA-256 hex digest identifying the document.

    Returns:
        Response with the serialized remediation job (id, document_id, original_filename,
        status, error, created_at, started_at, completed_at).
        Returns 404 if no remediation job for that file + that service account.
    """

    def get(self, request: Request, content_hash: str) -> Response:
        remediation = RemediationService().latest_for_document(self.service_account, content_hash)
        if remediation is None:
            raise Http404
        return Response(RemediationSerializer(remediation).data)


class DocumentDownloadView(ServiceAccountRequiredMixin):
    """
    Serves a document's most recent remediation attempt's finished output file.

    Serves it regardless of `status` (ADR 0013/0015) — a FAILED attempt can still have a
    partially-remediated file worth downloading — as long as one was actually produced.

    Takes:
        content_hash: the SHA-256 hex digest identifying the document.

    Returns:
        The PDF as an attachment. 404 if no remediation job for that file + that service
        account, or if nothing has been produced yet (e.g. still QUEUED/RUNNING).
    """

    def get(self, request: Request, content_hash: str) -> FileResponse:
        remediation = RemediationService().latest_for_document(self.service_account, content_hash)
        if remediation is None or not remediation.final_output_uri:
            raise Http404
        return FileResponse(
            default_storage.open(remediation.final_output_uri),
            content_type="application/pdf",
            filename=remediation.original_filename or "document.pdf",
            as_attachment=True,
        )
