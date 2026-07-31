from django.contrib.auth.models import AnonymousUser
from django.http import Http404
from rest_framework import status
from rest_framework.authentication import TokenAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import ServiceAccount
from remediation.serializers import RemediationSerializer, RemediationUploadSerializer
from remediation.services import RemediationService
from remediation.tasks import process_remediation


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

    Otherwise finds the most recent existing job for that file.

    Takes:
        a PDF document.

    Returns:
        Response with the serialized remediation job (id, document_id, original_filename,
        status, error, created_at, started_at, completed_at).
    """

    def post(self, request: Request) -> Response:
        upload = RemediationUploadSerializer(data=request.data)
        upload.is_valid(raise_exception=True)

        remediation, created = RemediationService().get_or_create_from_upload(
            self.service_account, upload.validated_data["file"]
        )
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
