from __future__ import annotations

import tempfile
from unittest.mock import create_autospec

from django.core.files.storage import default_storage
from django.db import IntegrityError
from django.test import TestCase, override_settings
from parameterized import parameterized

from accounts.tests.factories import ServiceAccountFactory
from remediation.adapters.base import AdapterError
from remediation.adapters.verification.vera_pdf import VeraPDFAdapter
from remediation.models import Remediation, RemediationArtifact
from remediation.services import (
    AlreadyCompliant,
    ArtifactService,
    NotCompliant,
    PostCheckService,
    PrecheckService,
    RemediationService,
)
from remediation.tests.factories import (
    PdfUploadFactory,
    RemediationArtifactFactory,
    RemediationFactory,
)


class RemediationServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.service_account = ServiceAccountFactory()

    def test_get_returns_remediation_by_id(self) -> None:
        remediation = RemediationFactory(service_account=self.service_account)

        found = RemediationService().get(str(remediation.id))

        self.assertEqual(found, remediation)

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


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class RemediationServiceGetOrCreateFromUploadTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.service_account = ServiceAccountFactory()

    def test_creates_new_remediation_and_saves_file_for_new_content(self) -> None:
        remediation, created = RemediationService().get_or_create_from_upload(
            self.service_account, PdfUploadFactory()
        )

        self.assertTrue(created)
        self.assertEqual(remediation.service_account, self.service_account)
        self.assertEqual(remediation.status, Remediation.JobStatus.QUEUED)
        self.assertTrue(default_storage.exists(remediation.source_pdf_uri))

    def test_returns_existing_remediation_without_touching_storage_for_duplicate(self) -> None:
        content = b"same bytes"
        existing, _ = RemediationService().get_or_create_from_upload(
            self.service_account, PdfUploadFactory(content=content)
        )

        remediation, created = RemediationService().get_or_create_from_upload(
            self.service_account, PdfUploadFactory(content=content)
        )

        self.assertFalse(created)
        self.assertEqual(remediation, existing)
        self.assertEqual(Remediation.objects.count(), 1)

    def test_reuses_content_hash_across_differently_named_files(self) -> None:
        content = b"same bytes"
        existing, _ = RemediationService().get_or_create_from_upload(
            self.service_account, PdfUploadFactory(name="first.pdf", content=content)
        )

        remediation, created = RemediationService().get_or_create_from_upload(
            self.service_account, PdfUploadFactory(name="second.pdf", content=content)
        )

        self.assertFalse(created)
        self.assertEqual(remediation, existing)


class ArtifactServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.remediation = RemediationFactory()
        cls.service = ArtifactService()
        cls.service.step = RemediationArtifact.Step.OCR

    def test_mark_completed_records_completed_status_and_output_uri(self) -> None:
        artifact = self.service.mark_completed(self.remediation, "local:///tmp/output.pdf")

        self.assertEqual(artifact.remediation, self.remediation)
        self.assertEqual(artifact.step, RemediationArtifact.Step.OCR)
        self.assertEqual(artifact.status, RemediationArtifact.StepStatus.COMPLETED)
        self.assertEqual(artifact.output_uri, "local:///tmp/output.pdf")
        self.assertEqual(artifact.error, "")

    def test_mark_skipped_records_skipped_status_and_reason(self) -> None:
        artifact = self.service.mark_skipped(self.remediation, "no untagged figures")

        self.assertEqual(artifact.remediation, self.remediation)
        self.assertEqual(artifact.step, RemediationArtifact.Step.OCR)
        self.assertEqual(artifact.status, RemediationArtifact.StepStatus.SKIPPED)
        self.assertEqual(artifact.error, "no untagged figures")
        self.assertEqual(artifact.output_uri, "")

    def test_mark_failed_records_failed_status_and_error(self) -> None:
        artifact = self.service.mark_failed(self.remediation, "Timeout error during OCR")

        self.assertEqual(artifact.remediation, self.remediation)
        self.assertEqual(artifact.step, RemediationArtifact.Step.OCR)
        self.assertEqual(artifact.status, RemediationArtifact.StepStatus.FAILED)
        self.assertEqual(artifact.error, "Timeout error during OCR")
        self.assertEqual(artifact.output_uri, "")

    def test_mark_completed_for_step_already_recorded_violates_unique_constraint(self) -> None:
        RemediationArtifactFactory(remediation=self.remediation, step=RemediationArtifact.Step.OCR)

        with self.assertRaises(IntegrityError):
            self.service.mark_completed(self.remediation, "local:///tmp/output-2.pdf")

    @parameterized.expand(
        [
            ("enabled", True, False),
            ("disabled", False, True),
        ]
    )
    def test_is_disabled_reflects_setting(self, _name, setting_value, expected) -> None:
        with override_settings(RUN_OCR=setting_value):
            self.assertEqual(self.service.is_disabled(), expected)

    def test_record_skip_records_skipped_status_with_setting_name_reason(self) -> None:
        artifact = self.service.record_skip(self.remediation)

        self.assertEqual(artifact.status, RemediationArtifact.StepStatus.SKIPPED)
        self.assertEqual(artifact.error, "RUN_OCR is disabled")


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class VerificationServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.remediation = RemediationFactory(source_pdf_uri="remediations/test.pdf")

    def setUp(self) -> None:
        self.adapter = create_autospec(VeraPDFAdapter, spec_set=True)

    @parameterized.expand(
        [
            (
                "precheck_compliant_raises_already_compliant",
                PrecheckService,
                True,
                AlreadyCompliant,
            ),
            ("precheck_noncompliant_continues", PrecheckService, False, None),
            ("postcheck_compliant_continues", PostCheckService, True, None),
            ("postcheck_noncompliant_raises_not_compliant", PostCheckService, False, NotCompliant),
        ]
    )
    def test_run_signals_based_on_compliance(
        self, _name, service_cls, is_compliant, expected_exception
    ) -> None:
        self.adapter.validate.return_value = (is_compliant, "<report/>")
        service = service_cls(adapter=self.adapter)

        if expected_exception is not None:
            with self.assertRaises(expected_exception):
                service.run(self.remediation, pdf_uri="remediations/test.pdf")
        else:
            result = service.run(self.remediation, pdf_uri="remediations/test.pdf")
            self.assertEqual(result, "remediations/test.pdf")

        artifact = self.remediation.artifacts.get(step=service.step)
        self.assertEqual(artifact.status, RemediationArtifact.StepStatus.COMPLETED)
        self.assertEqual(artifact.output_uri, "remediations/test.pdf")

    @parameterized.expand(
        [
            ("precheck", PrecheckService),
            ("postcheck", PostCheckService),
        ]
    )
    def test_run_records_failed_artifact_and_reraises_on_adapter_error(
        self, _name, service_cls
    ) -> None:
        self.adapter.validate.side_effect = AdapterError("boom")
        service = service_cls(adapter=self.adapter)

        with self.assertRaises(AdapterError):
            service.run(self.remediation, pdf_uri="remediations/test.pdf")

        artifact = self.remediation.artifacts.get(step=service.step)
        self.assertEqual(artifact.status, RemediationArtifact.StepStatus.FAILED)
        self.assertEqual(artifact.error, "boom")
