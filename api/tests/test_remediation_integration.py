"""
End-to-end smoke tests for the real upload -> service -> storage -> task pipeline.

Unlike test_remediation_views.py (which mocks RemediationService to isolate
view logic) and test_services.py/test_tasks.py (which isolate their own
layers), nothing here is mocked.
"""

from __future__ import annotations

import tempfile

from django.core.files.storage import default_storage
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIRequestFactory

from accounts.tests.factories import ServiceAccountFactory
from api.views import CreateRemediationView
from remediation.models import Remediation
from remediation.tests.factories import PdfUploadFactory


@override_settings(
    MEDIA_ROOT=tempfile.mkdtemp(),
    TASKS={"default": {"BACKEND": "django.tasks.backends.immediate.ImmediateBackend"}},
    RUN_PRECHECK=False,
    RUN_OCR=False,
    RUN_FINALIZE_METADATA=False,
    RUN_ALT_TEXT=False,
    RUN_LINK_TAG=False,
    RUN_POSTCHECK=False,
)
class CreateRemediationIntegrationTests(TestCase):
    """Smoke tests the upload -> service - > storage task wiring to make sure a job
    runs end to end. Doesn't actually perform any compliance operations."""

    def setUp(self) -> None:
        self.url = reverse("submit-document")
        self.service_account = ServiceAccountFactory()

    def test_new_upload_is_saved_processed_and_marked_complete(self) -> None:
        request = APIRequestFactory().post(
            self.url,
            {"file": PdfUploadFactory()},
            format="multipart",
            HTTP_AUTHORIZATION=f"Token {self.service_account.token}",
        )

        response = CreateRemediationView.as_view()(request)

        self.assertEqual(response.status_code, 201)
        remediation = Remediation.objects.get(id=response.data["id"])
        self.assertEqual(remediation.service_account, self.service_account)
        self.assertEqual(remediation.original_filename, "test.pdf")
        self.assertTrue(default_storage.exists(remediation.source_pdf_uri))
        self.assertEqual(remediation.status, Remediation.JobStatus.COMPLETE)
