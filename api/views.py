import hashlib

from django.contrib.auth.models import AnonymousUser
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import UploadedFile
from django.http import Http404
from rest_framework import status
from rest_framework.authentication import TokenAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import ServiceAccount
from remediation.serializers import RemediationSerializer
from remediation.services import RemediationService


def _service_account(request: Request) -> ServiceAccount:
    # IsAuthenticated already guarantees this; narrows the type for mypy.
    assert not isinstance(request.user, AnonymousUser)
    return request.user.serviceaccount


class StatusView(APIView):
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        return Response({"status": "ok"})


def _hash_file(uploaded_file: UploadedFile) -> str:
    hasher = hashlib.sha256()
    for chunk in uploaded_file.chunks():
        hasher.update(chunk)
    uploaded_file.seek(0)
    return hasher.hexdigest()


class CreateRemediationView(APIView):
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request: Request) -> Response:
        uploaded_file = request.FILES.get("file")
        if uploaded_file is None:
            return Response({"detail": "file is required."}, status=status.HTTP_400_BAD_REQUEST)
        if not uploaded_file.name.lower().endswith(".pdf"):
            return Response({"detail": "file must be a PDF."}, status=status.HTTP_400_BAD_REQUEST)

        service = RemediationService()
        content_hash = _hash_file(uploaded_file)
        existing = service.find_existing(_service_account(request), content_hash)
        if existing is not None:
            return Response(RemediationSerializer(existing).data, status=status.HTTP_200_OK)

        name = default_storage.save(f"remediations/{uploaded_file.name}", uploaded_file)
        remediation = service.create(
            _service_account(request), source_pdf_uri=name, content_hash=content_hash
        )
        return Response(RemediationSerializer(remediation).data, status=status.HTTP_201_CREATED)


class DocumentStatusView(APIView):
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request: Request, content_hash: str) -> Response:
        remediation = RemediationService().latest_for_document(
            _service_account(request), content_hash
        )
        if remediation is None:
            raise Http404
        return Response(RemediationSerializer(remediation).data)
