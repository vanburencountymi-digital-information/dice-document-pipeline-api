from __future__ import annotations

import os
import tempfile
from unittest.mock import create_autospec

from django.core.files.storage import default_storage
from django.db import IntegrityError
from django.test import TestCase, override_settings
from parameterized import parameterized

from accounts.tests.factories import ServiceAccountFactory
from remediation.adapters.alt_text.claude_vision import ClaudeVisionClient
from remediation.adapters.alt_text.pike_pdf import PikePdfAdapter as AltTextPikePdfAdapter
from remediation.adapters.base import AdapterError, FigureCandidate, ScoringResult
from remediation.adapters.font_repair.pike_pdf import PikePdfAdapter as FontRepairPikePdfAdapter
from remediation.adapters.link.pike_pdf import PikePdfAdapter as LinkPikePdfAdapter
from remediation.adapters.metadata.pike_pdf import PikePdfAdapter
from remediation.adapters.ocr.open_data_loader import OpenDataLoaderAdapter
from remediation.adapters.scoring.pike_pdf import PikePdfAdapter as ScoringPikePdfAdapter
from remediation.adapters.verification.vera_pdf import VeraPDFAdapter
from remediation.models import (
    Remediation,
    RemediationArtifact,
    RemediationScore,
    VerificationResult,
)
from remediation.services import (
    DEFAULT_TITLE,
    AlreadyCompliant,
    AltTextService,
    ArtifactService,
    FontRepairService,
    LinkService,
    MetadataService,
    NotCompliant,
    OCRService,
    PostCheckService,
    PrecheckService,
    RemediationService,
    ScoringService,
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

        result_row = VerificationResult.objects.get(remediation=self.remediation, step=service.step)
        self.assertEqual(result_row.is_compliant, is_compliant)

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


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class MetadataServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.service_account = ServiceAccountFactory()
        cls.remediation = RemediationFactory(
            service_account=cls.service_account,
            content_hash="abc123",
            source_pdf_uri=f"remediations/{cls.service_account.id}/abc123/test.pdf",
            original_filename="test.pdf",
        )

    def setUp(self) -> None:
        self.adapter = create_autospec(PikePdfAdapter, spec_set=True)

    def _output_dir(self) -> str:
        return MetadataService(adapter=self.adapter).construct_output_dir(self.remediation)

    def test_run_returns_output_uri_and_records_completed_artifact(self) -> None:
        output_dir = self._output_dir()
        finalized_path = os.path.join(output_dir, "test.pdf")
        self.adapter.finalize.return_value = finalized_path

        result = MetadataService(adapter=self.adapter).run(
            self.remediation, pdf_uri=self.remediation.source_pdf_uri
        )

        expected_uri = (
            f"remediations/{self.service_account.id}/abc123/"
            f"{self.remediation.id}/finalize_metadata/test.pdf"
        )
        self.assertEqual(result, expected_uri)
        artifact = self.remediation.artifacts.get(step=RemediationArtifact.Step.FINALIZE_METADATA)
        self.assertEqual(artifact.status, RemediationArtifact.StepStatus.COMPLETED)
        self.assertEqual(artifact.output_uri, expected_uri)

    def test_run_calls_adapter_with_title_derived_from_original_filename(self) -> None:
        output_dir = self._output_dir()
        self.adapter.finalize.return_value = os.path.join(output_dir, "test.pdf")

        MetadataService(adapter=self.adapter).run(
            self.remediation, pdf_uri=self.remediation.source_pdf_uri
        )

        self.adapter.finalize.assert_called_once_with(
            default_storage.path(self.remediation.source_pdf_uri),
            output_dir=output_dir,
            title="test",
            lang="en-us",
        )

    def test_run_falls_back_to_default_title_when_original_filename_blank(self) -> None:
        remediation = RemediationFactory(
            service_account=self.service_account, content_hash="def456", original_filename=""
        )
        output_dir = MetadataService(adapter=self.adapter).construct_output_dir(remediation)
        self.adapter.finalize.return_value = os.path.join(output_dir, "test.pdf")

        MetadataService(adapter=self.adapter).run(remediation, pdf_uri=remediation.source_pdf_uri)

        self.assertEqual(self.adapter.finalize.call_args.kwargs["title"], DEFAULT_TITLE)

    def test_run_records_failed_artifact_and_reraises_on_adapter_error(self) -> None:
        self.adapter.finalize.side_effect = AdapterError("boom")

        with self.assertRaises(AdapterError):
            MetadataService(adapter=self.adapter).run(
                self.remediation, pdf_uri=self.remediation.source_pdf_uri
            )

        artifact = self.remediation.artifacts.get(step=RemediationArtifact.Step.FINALIZE_METADATA)
        self.assertEqual(artifact.status, RemediationArtifact.StepStatus.FAILED)
        self.assertEqual(artifact.error, "boom")

    def test_default_adapter_is_pike_pdf_adapter(self) -> None:
        service = MetadataService()

        self.assertIsInstance(service.adapter, PikePdfAdapter)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class FontRepairServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.service_account = ServiceAccountFactory()
        cls.remediation = RemediationFactory(
            service_account=cls.service_account,
            content_hash="abc123",
            source_pdf_uri=f"remediations/{cls.service_account.id}/abc123/test.pdf",
        )

    def setUp(self) -> None:
        self.adapter = create_autospec(FontRepairPikePdfAdapter, spec_set=True)

    def _output_dir(self) -> str:
        return FontRepairService(adapter=self.adapter).construct_output_dir(self.remediation)

    def test_run_returns_output_uri_and_records_completed_artifact(self) -> None:
        output_dir = self._output_dir()
        repaired_path = os.path.join(output_dir, "test.pdf")
        self.adapter.repair.return_value = repaired_path

        result = FontRepairService(adapter=self.adapter).run(
            self.remediation, pdf_uri=self.remediation.source_pdf_uri
        )

        expected_uri = (
            f"remediations/{self.service_account.id}/abc123/{self.remediation.id}/"
            "font_repair/test.pdf"
        )
        self.assertEqual(result, expected_uri)
        artifact = self.remediation.artifacts.get(step=RemediationArtifact.Step.FONT_REPAIR)
        self.assertEqual(artifact.status, RemediationArtifact.StepStatus.COMPLETED)
        self.assertEqual(artifact.output_uri, expected_uri)

    def test_run_calls_adapter_with_correct_args(self) -> None:
        output_dir = self._output_dir()
        self.adapter.repair.return_value = os.path.join(output_dir, "test.pdf")

        FontRepairService(adapter=self.adapter).run(
            self.remediation, pdf_uri=self.remediation.source_pdf_uri
        )

        self.adapter.repair.assert_called_once_with(
            default_storage.path(self.remediation.source_pdf_uri), output_dir=output_dir
        )

    def test_run_records_failed_artifact_and_reraises_on_adapter_error(self) -> None:
        self.adapter.repair.side_effect = AdapterError("boom")

        with self.assertRaises(AdapterError):
            FontRepairService(adapter=self.adapter).run(
                self.remediation, pdf_uri=self.remediation.source_pdf_uri
            )

        artifact = self.remediation.artifacts.get(step=RemediationArtifact.Step.FONT_REPAIR)
        self.assertEqual(artifact.status, RemediationArtifact.StepStatus.FAILED)
        self.assertEqual(artifact.error, "boom")

    def test_default_adapter_is_font_repair_pike_pdf_adapter(self) -> None:
        service = FontRepairService()

        self.assertIsInstance(service.adapter, FontRepairPikePdfAdapter)


class LinkServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.service_account = ServiceAccountFactory()
        cls.remediation = RemediationFactory(
            service_account=cls.service_account,
            content_hash="abc123",
            source_pdf_uri=f"remediations/{cls.service_account.id}/abc123/test.pdf",
        )

    def setUp(self) -> None:
        self.adapter = create_autospec(LinkPikePdfAdapter, spec_set=True)

    def _output_dir(self) -> str:
        return LinkService(adapter=self.adapter).construct_output_dir(self.remediation)

    def test_run_returns_output_uri_and_records_completed_artifact(self) -> None:
        output_dir = self._output_dir()
        repaired_path = os.path.join(output_dir, "test.pdf")
        self.adapter.repair.return_value = repaired_path

        result = LinkService(adapter=self.adapter).run(
            self.remediation, pdf_uri=self.remediation.source_pdf_uri
        )

        expected_uri = (
            f"remediations/{self.service_account.id}/abc123/{self.remediation.id}/link_tag/test.pdf"
        )
        self.assertEqual(result, expected_uri)
        artifact = self.remediation.artifacts.get(step=RemediationArtifact.Step.LINK_TAG)
        self.assertEqual(artifact.status, RemediationArtifact.StepStatus.COMPLETED)
        self.assertEqual(artifact.output_uri, expected_uri)

    def test_run_calls_adapter_with_correct_args(self) -> None:
        output_dir = self._output_dir()
        self.adapter.repair.return_value = os.path.join(output_dir, "test.pdf")

        LinkService(adapter=self.adapter).run(
            self.remediation, pdf_uri=self.remediation.source_pdf_uri
        )

        self.adapter.repair.assert_called_once_with(
            default_storage.path(self.remediation.source_pdf_uri), output_dir=output_dir
        )

    def test_run_records_failed_artifact_and_reraises_on_adapter_error(self) -> None:
        self.adapter.repair.side_effect = AdapterError("boom")

        with self.assertRaises(AdapterError):
            LinkService(adapter=self.adapter).run(
                self.remediation, pdf_uri=self.remediation.source_pdf_uri
            )

        artifact = self.remediation.artifacts.get(step=RemediationArtifact.Step.LINK_TAG)
        self.assertEqual(artifact.status, RemediationArtifact.StepStatus.FAILED)
        self.assertEqual(artifact.error, "boom")

    def test_default_adapter_is_link_pike_pdf_adapter(self) -> None:
        service = LinkService()

        self.assertIsInstance(service.adapter, LinkPikePdfAdapter)


class AltTextServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.service_account = ServiceAccountFactory()
        cls.remediation = RemediationFactory(
            service_account=cls.service_account,
            content_hash="abc123",
            source_pdf_uri=f"remediations/{cls.service_account.id}/abc123/test.pdf",
            original_filename="test.pdf",
        )

    def setUp(self) -> None:
        self.adapter = create_autospec(AltTextPikePdfAdapter, spec_set=True)
        self.client = create_autospec(ClaudeVisionClient, spec_set=True)

    def _output_dir(self) -> str:
        return AltTextService(adapter=self.adapter, client=self.client).construct_output_dir(
            self.remediation
        )

    def test_run_returns_output_uri_and_records_completed_artifact(self) -> None:
        output_dir = self._output_dir()
        output_path = os.path.join(output_dir, "test.pdf")
        self.adapter.collect_figures.return_value = []
        self.adapter.write_alt_text.return_value = output_path

        result = AltTextService(adapter=self.adapter, client=self.client).run(
            self.remediation, pdf_uri=self.remediation.source_pdf_uri
        )

        expected_uri = (
            f"remediations/{self.service_account.id}/abc123/{self.remediation.id}/alt_text/test.pdf"
        )
        self.assertEqual(result, expected_uri)
        artifact = self.remediation.artifacts.get(step=RemediationArtifact.Step.ALT_TEXT)
        self.assertEqual(artifact.status, RemediationArtifact.StepStatus.COMPLETED)
        self.assertEqual(artifact.output_uri, expected_uri)

    def test_run_calls_describe_once_per_non_decorative_candidate(self) -> None:
        output_dir = self._output_dir()
        self.adapter.collect_figures.return_value = [
            FigureCandidate(
                ref=(0, 0),
                page_number=1,
                image_bytes=b"a",
                media_type="image/png",
                decorative=False,
            ),
            FigureCandidate(
                ref=(0, 1), page_number=1, image_bytes=b"", media_type="", decorative=True
            ),
        ]
        self.adapter.write_alt_text.return_value = os.path.join(output_dir, "test.pdf")
        self.client.describe.return_value = "a red square"

        AltTextService(adapter=self.adapter, client=self.client).run(
            self.remediation, pdf_uri=self.remediation.source_pdf_uri
        )

        self.client.describe.assert_called_once_with(
            b"a", media_type="image/png", document_title="test", page_number=1
        )
        self.adapter.write_alt_text.assert_called_once_with(
            default_storage.path(self.remediation.source_pdf_uri),
            output_dir=output_dir,
            alt_by_ref={(0, 0): "a red square", (0, 1): ""},
        )

    def test_run_records_failed_artifact_and_reraises_on_collect_figures_error(self) -> None:
        self.adapter.collect_figures.side_effect = AdapterError("boom")

        with self.assertRaises(AdapterError):
            AltTextService(adapter=self.adapter, client=self.client).run(
                self.remediation, pdf_uri=self.remediation.source_pdf_uri
            )

        artifact = self.remediation.artifacts.get(step=RemediationArtifact.Step.ALT_TEXT)
        self.assertEqual(artifact.status, RemediationArtifact.StepStatus.FAILED)
        self.assertEqual(artifact.error, "boom")

    def test_run_records_failed_artifact_and_reraises_on_describe_error(self) -> None:
        self.adapter.collect_figures.return_value = [
            FigureCandidate(
                ref=(0, 0),
                page_number=1,
                image_bytes=b"a",
                media_type="image/png",
                decorative=False,
            )
        ]
        self.client.describe.side_effect = AdapterError("rate limited")

        with self.assertRaises(AdapterError):
            AltTextService(adapter=self.adapter, client=self.client).run(
                self.remediation, pdf_uri=self.remediation.source_pdf_uri
            )

        artifact = self.remediation.artifacts.get(step=RemediationArtifact.Step.ALT_TEXT)
        self.assertEqual(artifact.status, RemediationArtifact.StepStatus.FAILED)
        self.assertEqual(artifact.error, "rate limited")

    def test_default_adapter_and_client(self) -> None:
        service = AltTextService()

        self.assertIsInstance(service.adapter, AltTextPikePdfAdapter)
        self.assertIsInstance(service.client, ClaudeVisionClient)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class ScoringServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.remediation = RemediationFactory(source_pdf_uri="remediations/test.pdf")

    def setUp(self) -> None:
        self.adapter = create_autospec(ScoringPikePdfAdapter, spec_set=True)

    def test_run_returns_pdf_uri_and_records_completed_artifact_and_score(self) -> None:
        self.adapter.score.return_value = ScoringResult(
            score=82, grade="B", manual_review_items=["fix contrast"]
        )

        result = ScoringService(adapter=self.adapter).run(
            self.remediation, pdf_uri="remediations/test.pdf"
        )

        self.assertEqual(result, "remediations/test.pdf")
        artifact = self.remediation.artifacts.get(step=RemediationArtifact.Step.SCORING)
        self.assertEqual(artifact.status, RemediationArtifact.StepStatus.COMPLETED)
        score = RemediationScore.objects.get(remediation=self.remediation)
        self.assertEqual(score.score, 82)
        self.assertEqual(score.grade, "B")
        self.assertEqual(score.manual_review_items, ["fix contrast"])

    @parameterized.expand(
        [
            ("adapter_error", AdapterError("boom"), "boom"),
            ("unexpected_error", RuntimeError("boom"), "unexpected scoring error: boom"),
        ]
    )
    def test_run_does_not_raise_and_records_failed_artifact_on_error(
        self, _name, side_effect, expected_error
    ) -> None:
        self.adapter.score.side_effect = side_effect

        result = ScoringService(adapter=self.adapter).run(
            self.remediation, pdf_uri="remediations/test.pdf"
        )

        self.assertEqual(result, "remediations/test.pdf")
        artifact = self.remediation.artifacts.get(step=RemediationArtifact.Step.SCORING)
        self.assertEqual(artifact.status, RemediationArtifact.StepStatus.FAILED)
        self.assertEqual(artifact.error, expected_error)
        self.assertFalse(RemediationScore.objects.filter(remediation=self.remediation).exists())

    def test_default_adapter_is_scoring_pike_pdf_adapter(self) -> None:
        service = ScoringService()

        self.assertIsInstance(service.adapter, ScoringPikePdfAdapter)
