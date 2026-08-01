from __future__ import annotations

import tempfile
from unittest.mock import patch

from django.core.files.storage import default_storage
from django.tasks import TaskResultStatus
from django.test import TestCase, override_settings

from remediation.models import Remediation, RemediationArtifact
from remediation.tasks import process_remediation
from remediation.tests.factories import RemediationFactory


@override_settings(
    TASKS={"default": {"BACKEND": "django.tasks.backends.immediate.ImmediateBackend"}},
    MEDIA_ROOT=tempfile.mkdtemp(),
)
class ProcessRemediationTaskTests(TestCase):
    @override_settings(RUN_PRECHECK=False, RUN_OCR=False, RUN_POSTCHECK=False)
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
                (RemediationArtifact.Step.POSTCHECK, RemediationArtifact.StepStatus.SKIPPED),
            },
        )

    def test_raises_for_unknown_remediation(self) -> None:
        result = process_remediation.enqueue("00000000-0000-0000-0000-000000000000")

        self.assertEqual(result.status, TaskResultStatus.FAILED)

    @override_settings(RUN_PRECHECK=True, RUN_OCR=False, RUN_POSTCHECK=True)
    @patch("remediation.services.VeraPDFAdapter", autospec=True)
    def test_completes_immediately_when_already_compliant(self, mock_adapter_cls) -> None:
        mock_adapter_cls.return_value.validate.return_value = (True, "<report/>")
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

    @override_settings(RUN_PRECHECK=True, RUN_OCR=False, RUN_POSTCHECK=True)
    @patch("remediation.services.VeraPDFAdapter", autospec=True)
    def test_fails_when_postcheck_still_noncompliant(self, mock_adapter_cls) -> None:
        mock_adapter_cls.return_value.validate.return_value = (False, "<report/>")
        remediation = RemediationFactory()

        result = process_remediation.enqueue(str(remediation.id))

        remediation.refresh_from_db()
        self.assertEqual(result.status, TaskResultStatus.SUCCESSFUL)
        self.assertEqual(remediation.status, Remediation.JobStatus.FAILED)
        self.assertEqual(remediation.error, "postcheck: not PDF/UA-1 compliant")
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

    @override_settings(RUN_PRECHECK=True, RUN_OCR=True, RUN_POSTCHECK=True)
    @patch("remediation.services.OpenDataLoaderAdapter", autospec=True)
    @patch("remediation.services.VeraPDFAdapter", autospec=True)
    def test_runs_ocr_between_precheck_and_postcheck_when_enabled(
        self, mock_vera_cls, mock_ocr_cls
    ) -> None:
        mock_vera_cls.return_value.validate.side_effect = [
            (False, "<report/>"),
            (True, "<report/>"),
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
