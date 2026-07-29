from __future__ import annotations

from django.test import TestCase

from accounts.tests.factories import ServiceAccountFactory
from remediation.models import Remediation
from remediation.services import RemediationService
from remediation.tests.factories import RemediationFactory


class RemediationServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.service_account = ServiceAccountFactory()

    def test_create_links_remediation_to_service_account_and_queues_it(self) -> None:
        remediation = RemediationService().create(
            self.service_account,
            source_pdf_uri="local:///tmp/document.pdf",
            content_hash="abc123",
        )

        self.assertEqual(remediation.service_account, self.service_account)
        self.assertEqual(remediation.source_pdf_uri, "local:///tmp/document.pdf")
        self.assertEqual(remediation.status, Remediation.JobStatus.QUEUED)

    def test_find_existing_returns_matching_non_failed_remediation(self) -> None:
        remediation = RemediationFactory(
            service_account=self.service_account, content_hash="abc123"
        )

        found = RemediationService().find_existing(self.service_account, "abc123")

        self.assertEqual(found, remediation)

    def test_find_existing_ignores_failed_remediation(self) -> None:
        RemediationFactory(
            service_account=self.service_account,
            content_hash="abc123",
            status=Remediation.JobStatus.FAILED,
        )

        found = RemediationService().find_existing(self.service_account, "abc123")

        self.assertIsNone(found)

    def test_find_existing_ignores_other_service_accounts(self) -> None:
        RemediationFactory(content_hash="abc123")

        found = RemediationService().find_existing(self.service_account, "abc123")

        self.assertIsNone(found)

    def test_latest_for_document_returns_most_recent_attempt(self) -> None:
        RemediationFactory(service_account=self.service_account, content_hash="abc123")
        newest = RemediationFactory(service_account=self.service_account, content_hash="abc123")

        found = RemediationService().latest_for_document(self.service_account, "abc123")

        self.assertEqual(found, newest)

    def test_latest_for_document_includes_failed_remediation(self) -> None:
        remediation = RemediationFactory(
            service_account=self.service_account,
            content_hash="abc123",
            status=Remediation.JobStatus.FAILED,
        )

        found = RemediationService().latest_for_document(self.service_account, "abc123")

        self.assertEqual(found, remediation)

    def test_latest_for_document_returns_none_when_no_match(self) -> None:
        found = RemediationService().latest_for_document(self.service_account, "abc123")

        self.assertIsNone(found)

    def test_latest_for_document_ignores_other_service_accounts(self) -> None:
        RemediationFactory(content_hash="abc123")

        found = RemediationService().latest_for_document(self.service_account, "abc123")

        self.assertIsNone(found)

    def test_mark_running_sets_status_and_started_at(self) -> None:
        remediation = RemediationFactory()

        RemediationService().mark_running(remediation)

        self.assertEqual(remediation.status, Remediation.JobStatus.RUNNING)
        self.assertIsNotNone(remediation.started_at)

    def test_mark_complete_sets_status_and_completed_at(self) -> None:
        remediation = RemediationFactory()

        RemediationService().mark_complete(remediation)

        self.assertEqual(remediation.status, Remediation.JobStatus.COMPLETE)
        self.assertIsNotNone(remediation.completed_at)

    def test_mark_failed_sets_status_error_and_completed_at(self) -> None:
        remediation = RemediationFactory()

        RemediationService().mark_failed(remediation, "Timeout error during OCR")

        self.assertEqual(remediation.status, Remediation.JobStatus.FAILED)
        self.assertEqual(remediation.error, "Timeout error during OCR")
        self.assertIsNotNone(remediation.completed_at)
