from __future__ import annotations

from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from remediation.services.task_queue_service import TaskQueueService
from remediation.tasks import process_remediation


@override_settings(TASKS={"default": {"BACKEND": "django_tasks_db.DatabaseBackend"}})
class TaskQueueServiceTests(TestCase):
    """ADR 0023 — what counts as "work to do" for a scheduled worker run."""

    def test_no_tasks_is_not_ready(self) -> None:
        self.assertFalse(TaskQueueService().has_ready_tasks())

    def test_enqueued_task_is_ready(self) -> None:
        process_remediation.enqueue("00000000-0000-0000-0000-000000000000")

        self.assertTrue(TaskQueueService().has_ready_tasks())

    def test_task_deferred_to_the_future_is_not_ready(self) -> None:
        process_remediation.using(run_after=timezone.now() + timedelta(hours=1)).enqueue(
            "00000000-0000-0000-0000-000000000000"
        )

        self.assertFalse(TaskQueueService().has_ready_tasks())
