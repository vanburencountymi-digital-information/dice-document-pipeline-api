from __future__ import annotations

import tempfile
from unittest.mock import patch

from django.core.files.storage import default_storage
from django.tasks import TaskResultStatus
from django.test import TestCase, override_settings

from remediation.adapters.base import AdapterError, FailedRule, VerificationOutcome
from remediation.adapters.verification.severity import Severity
from remediation.models import Remediation, RemediationArtifact
from remediation.tasks import process_remediation
from remediation.tests.factories import RemediationFactory


@override_settings(
    TASKS={"default": {"BACKEND": "django.tasks.backends.immediate.ImmediateBackend"}},
    MEDIA_ROOT=tempfile.mkdtemp(),
)
class ProcessRemediationTaskTests(TestCase):
    @override_settings(
        RUN_PRECHECK=False,
        RUN_OCR=False,
        RUN_FONT_REPAIR=False,
        RUN_FINALIZE_METADATA=False,
        RUN_LINK_TAG=False,
        RUN_ALT_TEXT=False,
        RUN_SCORING=False,
        RUN_POSTCHECK=False,
    )
    def test_marks_complete_when_all_steps_disabled(self) -> None:
        remediation = RemediationFactory()

        result = process_remediation.enqueue(str(remediation.id))

        remediation.refresh_from_db()
        self.assertEqual(result.status, TaskResultStatus.SUCCESSFUL)
        self.assertEqual(remediation.status, Remediation.JobStatus.COMPLETE)
        self.assertIsNotNone(remediation.started_at)
        self.assertIsNotNone(remediation.completed_at)
        self.assertEqual(
            set(remediation.artifacts.values_list("step", "status")),
            {
                (RemediationArtifact.Step.PRECHECK, RemediationArtifact.StepStatus.SKIPPED),
                (RemediationArtifact.Step.OCR, RemediationArtifact.StepStatus.SKIPPED),
                (RemediationArtifact.Step.FONT_REPAIR, RemediationArtifact.StepStatus.SKIPPED),
                (
                    RemediationArtifact.Step.FINALIZE_METADATA,
                    RemediationArtifact.StepStatus.SKIPPED,
                ),
                (RemediationArtifact.Step.LINK_TAG, RemediationArtifact.StepStatus.SKIPPED),
                (RemediationArtifact.Step.ALT_TEXT, RemediationArtifact.StepStatus.SKIPPED),
                (RemediationArtifact.Step.SCORING, RemediationArtifact.StepStatus.SKIPPED),
                (RemediationArtifact.Step.POSTCHECK, RemediationArtifact.StepStatus.SKIPPED),
            },
        )

    def test_raises_for_unknown_remediation(self) -> None:
        result = process_remediation.enqueue("00000000-0000-0000-0000-000000000000")

        self.assertEqual(result.status, TaskResultStatus.FAILED)

    @override_settings(
        RUN_PRECHECK=True,
        RUN_OCR=False,
        RUN_FONT_REPAIR=False,
        RUN_FINALIZE_METADATA=False,
        RUN_LINK_TAG=False,
        RUN_ALT_TEXT=False,
        RUN_SCORING=False,
        RUN_POSTCHECK=True,
    )
    @patch("remediation.services.VeraPDFAdapter", autospec=True)
    def test_completes_immediately_when_already_compliant(self, mock_adapter_cls) -> None:
        mock_adapter_cls.return_value.validate.return_value = VerificationOutcome(
            is_compliant=True, failed_rules=[], verapdf_version="1.30.2"
        )
        remediation = RemediationFactory()

        result = process_remediation.enqueue(str(remediation.id))

        remediation.refresh_from_db()
        self.assertEqual(result.status, TaskResultStatus.SUCCESSFUL)
        self.assertEqual(remediation.status, Remediation.JobStatus.COMPLETE)
        self.assertTrue(
            remediation.artifacts.filter(
                step=RemediationArtifact.Step.PRECHECK,
                status=RemediationArtifact.StepStatus.COMPLETED,
            ).exists()
        )
        self.assertFalse(remediation.artifacts.filter(step=RemediationArtifact.Step.OCR).exists())
        self.assertFalse(
            remediation.artifacts.filter(step=RemediationArtifact.Step.POSTCHECK).exists()
        )

    @override_settings(
        RUN_PRECHECK=True,
        RUN_OCR=False,
        RUN_FONT_REPAIR=False,
        RUN_FINALIZE_METADATA=False,
        RUN_LINK_TAG=False,
        RUN_ALT_TEXT=False,
        RUN_SCORING=False,
        RUN_POSTCHECK=True,
    )
    @patch("remediation.services.VeraPDFAdapter", autospec=True)
    def test_fails_when_postcheck_still_noncompliant(self, mock_adapter_cls) -> None:
        failed_rule = FailedRule(
            clause="7.1",
            test_number="11",
            description="StructTreeRoot missing",
            failed_checks=1,
            severity=Severity.CRITICAL,
        )
        mock_adapter_cls.return_value.validate.return_value = VerificationOutcome(
            is_compliant=False, failed_rules=[failed_rule], verapdf_version="1.30.2"
        )
        remediation = RemediationFactory()

        result = process_remediation.enqueue(str(remediation.id))

        remediation.refresh_from_db()
        self.assertEqual(result.status, TaskResultStatus.SUCCESSFUL)
        self.assertEqual(remediation.status, Remediation.JobStatus.FAILED)
        self.assertEqual(
            remediation.error,
            "postcheck: 1 rules failed, 1 checks\n"
            "  - CRITICAL     7.1 StructTreeRoot missing (1 checks)",
        )
        self.assertTrue(
            remediation.artifacts.filter(
                step=RemediationArtifact.Step.PRECHECK,
                status=RemediationArtifact.StepStatus.COMPLETED,
            ).exists()
        )
        self.assertTrue(
            remediation.artifacts.filter(
                step=RemediationArtifact.Step.POSTCHECK,
                status=RemediationArtifact.StepStatus.COMPLETED,
            ).exists()
        )

    @override_settings(
        RUN_PRECHECK=True,
        RUN_OCR=True,
        RUN_FONT_REPAIR=False,
        RUN_FINALIZE_METADATA=False,
        RUN_LINK_TAG=False,
        RUN_ALT_TEXT=False,
        RUN_SCORING=False,
        RUN_POSTCHECK=True,
    )
    @patch("remediation.services.OpenDataLoaderAdapter", autospec=True)
    @patch("remediation.services.VeraPDFAdapter", autospec=True)
    def test_runs_ocr_between_precheck_and_postcheck_when_enabled(
        self, mock_vera_cls, mock_ocr_cls
    ) -> None:
        mock_vera_cls.return_value.validate.side_effect = [
            VerificationOutcome(is_compliant=False, failed_rules=[], verapdf_version="1.30.2"),
            VerificationOutcome(is_compliant=True, failed_rules=[], verapdf_version="1.30.2"),
        ]
        remediation = RemediationFactory()
        mock_ocr_cls.return_value.extract.return_value = default_storage.path(
            f"remediations/{remediation.id}/ocr/test.pdf"
        )

        result = process_remediation.enqueue(str(remediation.id))

        remediation.refresh_from_db()
        self.assertEqual(result.status, TaskResultStatus.SUCCESSFUL)
        self.assertEqual(remediation.status, Remediation.JobStatus.COMPLETE)
        self.assertTrue(
            remediation.artifacts.filter(
                step=RemediationArtifact.Step.OCR,
                status=RemediationArtifact.StepStatus.COMPLETED,
            ).exists()
        )

    @override_settings(
        RUN_PRECHECK=True,
        RUN_OCR=False,
        RUN_FONT_REPAIR=False,
        RUN_FINALIZE_METADATA=False,
        RUN_LINK_TAG=False,
        RUN_ALT_TEXT=False,
        RUN_SCORING=True,
        RUN_POSTCHECK=True,
    )
    @patch("remediation.services.ScoringPikePdfAdapter", autospec=True)
    @patch("remediation.services.VeraPDFAdapter", autospec=True)
    def test_scoring_failure_does_not_block_postcheck(
        self, mock_vera_cls, mock_scoring_cls
    ) -> None:
        mock_vera_cls.return_value.validate.side_effect = [
            VerificationOutcome(is_compliant=False, failed_rules=[], verapdf_version="1.30.2"),
            VerificationOutcome(is_compliant=True, failed_rules=[], verapdf_version="1.30.2"),
        ]
        mock_scoring_cls.return_value.score.side_effect = AdapterError("boom")
        remediation = RemediationFactory()

        result = process_remediation.enqueue(str(remediation.id))

        remediation.refresh_from_db()
        self.assertEqual(result.status, TaskResultStatus.SUCCESSFUL)
        self.assertEqual(remediation.status, Remediation.JobStatus.COMPLETE)
        self.assertTrue(
            remediation.artifacts.filter(
                step=RemediationArtifact.Step.SCORING,
                status=RemediationArtifact.StepStatus.FAILED,
            ).exists()
        )
        self.assertTrue(
            remediation.artifacts.filter(
                step=RemediationArtifact.Step.POSTCHECK,
                status=RemediationArtifact.StepStatus.COMPLETED,
            ).exists()
        )

    @override_settings(
        RUN_PRECHECK=True,
        RUN_OCR=True,
        RUN_FONT_REPAIR=False,
        RUN_FINALIZE_METADATA=False,
        RUN_LINK_TAG=False,
        RUN_ALT_TEXT=False,
        RUN_SCORING=False,
        RUN_POSTCHECK=True,
    )
    @patch("remediation.services.OpenDataLoaderAdapter", autospec=True)
    @patch("remediation.services.VeraPDFAdapter", autospec=True)
    def test_ocr_failure_does_not_abort_the_pipeline(self, mock_vera_cls, mock_ocr_cls) -> None:
        """ADR 0013 — unlike ScoringService (always non-blocking), OCRService used to abort
        the whole task on any AdapterError. This confirms it no longer does.
        """
        mock_vera_cls.return_value.validate.side_effect = [
            VerificationOutcome(is_compliant=False, failed_rules=[], verapdf_version="1.30.2"),
            VerificationOutcome(is_compliant=True, failed_rules=[], verapdf_version="1.30.2"),
        ]
        mock_ocr_cls.return_value.extract.side_effect = AdapterError("boom")
        remediation = RemediationFactory()

        result = process_remediation.enqueue(str(remediation.id))

        remediation.refresh_from_db()
        self.assertEqual(result.status, TaskResultStatus.SUCCESSFUL)
        self.assertEqual(remediation.status, Remediation.JobStatus.COMPLETE)
        self.assertTrue(
            remediation.artifacts.filter(
                step=RemediationArtifact.Step.OCR,
                status=RemediationArtifact.StepStatus.FAILED,
            ).exists()
        )
        self.assertTrue(
            remediation.artifacts.filter(
                step=RemediationArtifact.Step.POSTCHECK,
                status=RemediationArtifact.StepStatus.COMPLETED,
            ).exists()
        )

    @override_settings(
        RUN_PRECHECK=True,
        RUN_OCR=False,
        RUN_FONT_REPAIR=False,
        RUN_FINALIZE_METADATA=False,
        RUN_LINK_TAG=False,
        RUN_ALT_TEXT=False,
        RUN_SCORING=False,
        RUN_POSTCHECK=True,
    )
    @patch("remediation.services.VeraPDFAdapter", autospec=True)
    def test_precheck_adapter_crash_continues_to_postcheck(self, mock_adapter_cls) -> None:
        """precheck's own adapter crashing (not just a real non-compliant verdict) still
        doesn't stop the pipeline — ADR 0013's handle_verification_error default.
        """
        mock_adapter_cls.return_value.validate.side_effect = [
            AdapterError("verapdf crashed"),
            VerificationOutcome(is_compliant=True, failed_rules=[], verapdf_version="1.30.2"),
        ]
        remediation = RemediationFactory()

        result = process_remediation.enqueue(str(remediation.id))

        remediation.refresh_from_db()
        self.assertEqual(result.status, TaskResultStatus.SUCCESSFUL)
        self.assertEqual(remediation.status, Remediation.JobStatus.COMPLETE)
        self.assertTrue(
            remediation.artifacts.filter(
                step=RemediationArtifact.Step.PRECHECK,
                status=RemediationArtifact.StepStatus.FAILED,
                error="verapdf crashed",
            ).exists()
        )

    @override_settings(
        RUN_PRECHECK=True,
        RUN_OCR=False,
        RUN_FONT_REPAIR=False,
        RUN_FINALIZE_METADATA=False,
        RUN_LINK_TAG=False,
        RUN_ALT_TEXT=False,
        RUN_SCORING=False,
        RUN_POSTCHECK=True,
    )
    @patch("remediation.services.VeraPDFAdapter", autospec=True)
    def test_postcheck_adapter_crash_fails_the_job_without_failing_the_task(
        self, mock_adapter_cls
    ) -> None:
        """postcheck's own adapter crashing still has to fail the job (there's no later
        check to fall back on, and it must never default to COMPLETE) — but per ADR 0013
        that's a NotCompliant, not a re-raised exception, so the task itself still succeeds.
        """
        mock_adapter_cls.return_value.validate.side_effect = [
            VerificationOutcome(is_compliant=False, failed_rules=[], verapdf_version="1.30.2"),
            AdapterError("verapdf crashed"),
        ]
        remediation = RemediationFactory()

        result = process_remediation.enqueue(str(remediation.id))

        remediation.refresh_from_db()
        self.assertEqual(result.status, TaskResultStatus.SUCCESSFUL)
        self.assertEqual(remediation.status, Remediation.JobStatus.FAILED)
        self.assertEqual(remediation.error, "postcheck: postcheck could not run: verapdf crashed")
        self.assertTrue(
            remediation.artifacts.filter(
                step=RemediationArtifact.Step.POSTCHECK,
                status=RemediationArtifact.StepStatus.FAILED,
                error="verapdf crashed",
            ).exists()
        )
