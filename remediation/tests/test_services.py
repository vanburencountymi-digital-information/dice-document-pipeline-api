from __future__ import annotations

import os
import tempfile
from unittest.mock import create_autospec

from django.core.files.storage import default_storage
from django.db import IntegrityError
from django.test import TestCase, override_settings
from parameterized import parameterized

from accounts.tests.factories import ServiceAccountFactory
from remediation.adapters.base import AdapterError
from remediation.adapters.ocr.open_data_loader import OpenDataLoaderAdapter
from remediation.adapters.verification.vera_pdf import VeraPDFAdapter
from remediation.models import Remediation, RemediationArtifact
from remediation.services import (
    AlreadyCompliant,
    ArtifactService,
    NotCompliant,
    OCRService,
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
            self.service_account, PdfUploadFactory(name="test.pdf")
        )

        self.assertTrue(created)
        self.assertEqual(remediation.service_account, self.service_account)
        self.assertEqual(remediation.status, Remediation.JobStatus.QUEUED)
        self.assertEqual(remediation.original_filename, "test.pdf")
        expected_uri = f"remediations/{self.service_account.id}/{remediation.content_hash}/test.pdf"
        self.assertEqual(remediation.source_pdf_uri, expected_uri)
        self.assertTrue(default_storage.exists(remediation.source_pdf_uri))

    def test_resubmitting_failed_remediation_reports_it_without_new_job_or_file(self) -> None:
        content = b"same bytes"
        failed, _ = RemediationService().get_or_create_from_upload(
            self.service_account, PdfUploadFactory(name="test.pdf", content=content)
        )
        failed.status = Remediation.JobStatus.FAILED
        failed.save(update_fields=["status"])
        original_path = failed.source_pdf_uri

        remediation, created = RemediationService().get_or_create_from_upload(
            self.service_account, PdfUploadFactory(name="test.pdf", content=content)
        )

        self.assertFalse(created)
        self.assertEqual(remediation, failed)
        self.assertEqual(remediation.status, Remediation.JobStatus.FAILED)
        self.assertEqual(Remediation.objects.count(), 1)
        self.assertEqual(remediation.source_pdf_uri, original_path)

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

    def test_construct_output_dir_keys_path_by_document_and_step(self) -> None:
        remediation = RemediationFactory(content_hash="abc123")

        output_dir = self.service.construct_output_dir(remediation)

        expected = default_storage.path(
            f"remediations/{remediation.service_account_id}/abc123/{remediation.id}/ocr"
        )
        self.assertEqual(output_dir, expected)

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


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class OCRServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.service_account = ServiceAccountFactory()
        cls.remediation = RemediationFactory(
            service_account=cls.service_account,
            content_hash="abc123",
            source_pdf_uri=f"remediations/{cls.service_account.id}/abc123/test.pdf",
        )

    def setUp(self) -> None:
        self.adapter = create_autospec(OpenDataLoaderAdapter, spec_set=True)

    def _output_dir(self) -> str:
        return OCRService(adapter=self.adapter).construct_output_dir(self.remediation)

    def test_run_returns_output_uri_and_records_completed_artifact(self) -> None:
        output_dir = self._output_dir()
        extracted_path = os.path.join(output_dir, "test.pdf")
        self.adapter.extract.return_value = extracted_path

        result = OCRService(adapter=self.adapter).run(
            self.remediation, pdf_uri=self.remediation.source_pdf_uri
        )

        expected_uri = (
            f"remediations/{self.service_account.id}/abc123/{self.remediation.id}/ocr/test.pdf"
        )
        self.assertEqual(result, expected_uri)
        self.adapter.extract.assert_called_once_with(
            default_storage.path(self.remediation.source_pdf_uri), output_dir=output_dir
        )
        artifact = self.remediation.artifacts.get(step=RemediationArtifact.Step.OCR)
        self.assertEqual(artifact.status, RemediationArtifact.StepStatus.COMPLETED)
        self.assertEqual(artifact.output_uri, expected_uri)

    def test_run_records_failed_artifact_and_reraises_on_adapter_error(self) -> None:
        self.adapter.extract.side_effect = AdapterError("boom")

        with self.assertRaises(AdapterError):
            OCRService(adapter=self.adapter).run(
                self.remediation, pdf_uri=self.remediation.source_pdf_uri
            )

        artifact = self.remediation.artifacts.get(step=RemediationArtifact.Step.OCR)
        self.assertEqual(artifact.status, RemediationArtifact.StepStatus.FAILED)
        self.assertEqual(artifact.error, "boom")

    def test_default_adapter_uses_hybrid_url_setting(self) -> None:
        with override_settings(OPENDATALOADER_HYBRID_URL="http://opendataloader-hybrid:5002"):
            service = OCRService()

        self.assertIsInstance(service.adapter, OpenDataLoaderAdapter)
        self.assertEqual(service.adapter.hybrid_url, "http://opendataloader-hybrid:5002")
