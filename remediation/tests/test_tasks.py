from __future__ import annotations

from django.tasks import TaskResultStatus
from django.test import TestCase, override_settings

from remediation.models import Remediation
from remediation.tasks import process_remediation
from remediation.tests.factories import RemediationFactory


@override_settings(
    TASKS={"default": {"BACKEND": "django.tasks.backends.immediate.ImmediateBackend"}}
)
class ProcessRemediationTaskTests(TestCase):
    def test_process_remediation_marks_complete(self) -> None:
        remediation = RemediationFactory()

        result = process_remediation.enqueue(str(remediation.id))

        remediation.refresh_from_db()
        self.assertEqual(result.status, TaskResultStatus.SUCCESSFUL)
        self.assertEqual(remediation.status, Remediation.JobStatus.COMPLETE)
        self.assertIsNotNone(remediation.started_at)
        self.assertIsNotNone(remediation.completed_at)

    def test_process_remediation_raises_for_unknown_remediation(self) -> None:
        result = process_remediation.enqueue("00000000-0000-0000-0000-000000000000")

        self.assertEqual(result.status, TaskResultStatus.FAILED)
