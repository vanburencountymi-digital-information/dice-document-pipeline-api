from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIRequestFactory

from accounts.tests.factories import ServiceAccountFactory
from api.views import CreateRemediationView, DocumentStatusView
from remediation.tests.factories import PdfUploadFactory, RemediationFactory


class CreateRemediationViewTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.url = reverse("submit-document")
        cls.service_account = ServiceAccountFactory()

    def setUp(self) -> None:
        self.factory = APIRequestFactory()
        self.view = CreateRemediationView.as_view()

    def _post(self, data):
        request = self.factory.post(
            self.url,
            data,
            format="multipart",
            HTTP_AUTHORIZATION=f"Token {self.service_account.token}",
        )
        return self.view(request)

    @patch("api.views.RemediationService", autospec=True, spec_set=True)
    def test_create_remediation_without_file_returns_400(self, mock_service_cls) -> None:
        response = self._post({})

        self.assertEqual(response.status_code, 400)
        mock_service_cls.assert_not_called()

    @patch("api.views.RemediationService", autospec=True, spec_set=True)
    def test_create_remediation_rejects_non_pdf_returns_400(self, mock_service_cls) -> None:
        uploaded_file = PdfUploadFactory(
            name="test.txt", content=b"not a pdf", content_type="text/plain"
        )

        response = self._post({"file": uploaded_file})

        self.assertEqual(response.status_code, 400)
        mock_service_cls.assert_not_called()

    @patch("api.views.process_remediation", autospec=True, spec_set=True)
    @patch("api.views.RemediationService", autospec=True, spec_set=True)
    def test_new_upload_calls_service_enqueues_task_and_returns_201(
        self, mock_service_cls, mock_process_remediation
    ) -> None:

        remediation = RemediationFactory()
        mock_service_cls.return_value.get_or_create_from_upload.return_value = (
            remediation,
            True,
        )

        response = self._post({"file": PdfUploadFactory()})

        service_account, uploaded_file = (
            mock_service_cls.return_value.get_or_create_from_upload.call_args.args
        )
        self.assertEqual(service_account, self.service_account)
        self.assertEqual(uploaded_file.name, "test.pdf")
        mock_process_remediation.enqueue.assert_called_once_with(str(remediation.id))
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["id"], str(remediation.id))

    @patch("api.views.process_remediation", autospec=True, spec_set=True)
    @patch("api.views.RemediationService", autospec=True, spec_set=True)
    def test_existing_upload_skips_enqueue_and_returns_200(
        self, mock_service_cls, mock_process_remediation
    ) -> None:
        remediation = RemediationFactory.build()
        mock_service_cls.return_value.get_or_create_from_upload.return_value = (
            remediation,
            False,
        )

        response = self._post({"file": PdfUploadFactory()})

        mock_process_remediation.enqueue.assert_not_called()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["id"], str(remediation.id))


class DocumentStatusViewTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.service_account = ServiceAccountFactory()

    def setUp(self) -> None:
        self.factory = APIRequestFactory()
        self.view = DocumentStatusView.as_view()

    def _get(self, content_hash):
        url = reverse("document-status", kwargs={"content_hash": content_hash})
        request = self.factory.get(url, HTTP_AUTHORIZATION=f"Token {self.service_account.token}")
        return self.view(request, content_hash=content_hash)

    @patch("api.views.RemediationService", autospec=True, spec_set=True)
    def test_returns_serialized_remediation_when_service_finds_one(self, mock_service_cls) -> None:
        remediation = RemediationFactory.build(content_hash="abc123")
        mock_service_cls.return_value.latest_for_document.return_value = remediation

        response = self._get("abc123")

        mock_service_cls.return_value.latest_for_document.assert_called_once_with(
            self.service_account, "abc123"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["id"], str(remediation.id))

    @patch("api.views.RemediationService", autospec=True, spec_set=True)
    def test_returns_404_when_service_finds_nothing(self, mock_service_cls) -> None:
        mock_service_cls.return_value.latest_for_document.return_value = None

        response = self._get("abc123")

        self.assertEqual(response.status_code, 404)
