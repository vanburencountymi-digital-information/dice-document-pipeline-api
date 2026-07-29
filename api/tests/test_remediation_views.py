from __future__ import annotations

import hashlib
import tempfile

from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIRequestFactory

from accounts.tests.factories import ServiceAccountFactory
from api.views import CreateRemediationView, DocumentStatusView
from remediation.models import Remediation
from remediation.tests.factories import RemediationFactory


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class CreateRemediationViewTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.url = reverse("submit-document")

    def setUp(self) -> None:
        self.factory = APIRequestFactory()
        self.view = CreateRemediationView.as_view()
        self.service_account = ServiceAccountFactory()

    def _post(self, uploaded_file):
        request = self.factory.post(
            self.url,
            {"file": uploaded_file},
            format="multipart",
            HTTP_AUTHORIZATION=f"Token {self.service_account.token}",
        )
        return self.view(request)

    def test_create_remediation_saves_file_and_returns_201(self) -> None:
        uploaded_file = SimpleUploadedFile(
            "test.pdf", b"%PDF-1.4 fake content", content_type="application/pdf"
        )

        response = self._post(uploaded_file)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["status"], "queued")
        self.assertTrue(response.data["document_id"])

        remediation = Remediation.objects.get(id=response.data["id"])
        self.assertEqual(remediation.service_account, self.service_account)
        self.assertTrue(default_storage.exists(remediation.source_pdf_uri))

    def test_create_remediation_without_file_returns_400(self) -> None:
        request = self.factory.post(
            self.url,
            {},
            format="multipart",
            HTTP_AUTHORIZATION=f"Token {self.service_account.token}",
        )
        response = self.view(request)

        self.assertEqual(response.status_code, 400)

    def test_create_remediation_rejects_non_pdf_returns_400(self) -> None:
        uploaded_file = SimpleUploadedFile("test.txt", b"not a pdf", content_type="text/plain")

        response = self._post(uploaded_file)

        self.assertEqual(response.status_code, 400)

    def test_create_remediation_duplicate_upload_returns_existing_with_200(self) -> None:
        first_response = self._post(
            SimpleUploadedFile("test.pdf", b"same bytes", content_type="application/pdf")
        )
        second_response = self._post(
            SimpleUploadedFile("test.pdf", b"same bytes", content_type="application/pdf")
        )

        self.assertEqual(first_response.status_code, 201)
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(second_response.data["id"], first_response.data["id"])
        self.assertEqual(Remediation.objects.count(), 1)

    def test_create_remediation_duplicate_of_failed_creates_new_attempt(self) -> None:
        content = b"same bytes"
        RemediationFactory(
            service_account=self.service_account,
            content_hash=hashlib.sha256(content).hexdigest(),
            status=Remediation.JobStatus.FAILED,
        )

        response = self._post(
            SimpleUploadedFile("test.pdf", content, content_type="application/pdf")
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(Remediation.objects.count(), 2)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class DocumentStatusViewTests(TestCase):
    def setUp(self) -> None:
        self.factory = APIRequestFactory()
        self.view = DocumentStatusView.as_view()
        self.service_account = ServiceAccountFactory()

    def _get(self, content_hash, service_account=None):
        service_account = service_account or self.service_account
        url = reverse("document-status", kwargs={"content_hash": content_hash})
        request = self.factory.get(url, HTTP_AUTHORIZATION=f"Token {service_account.token}")
        return self.view(request, content_hash=content_hash)

    def test_get_document_status_returns_latest_attempt(self) -> None:
        RemediationFactory(service_account=self.service_account, content_hash="abc123")
        newest = RemediationFactory(service_account=self.service_account, content_hash="abc123")

        response = self._get("abc123")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["id"], str(newest.id))

    def test_get_document_status_returns_404_when_no_match(self) -> None:
        response = self._get("abc123")

        self.assertEqual(response.status_code, 404)

    def test_get_document_status_scoped_to_service_account(self) -> None:
        other_service_account = ServiceAccountFactory()
        RemediationFactory(service_account=other_service_account, content_hash="abc123")

        response = self._get("abc123", service_account=self.service_account)

        self.assertEqual(response.status_code, 404)
