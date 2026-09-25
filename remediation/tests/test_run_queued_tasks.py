from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings
from parameterized import parameterized

from remediation.hybrid_client import HybridServerNotReady

COMMAND_MODULE = "remediation.management.commands.run_queued_tasks"


def patch_boundaries(test):
    """Mocks the command's three collaborators; the test receives them as
    (..., mock_queue_cls, mock_client_cls, mock_call_command).
    """
    # Innermost patch = first mock argument, so apply them in argument order.
    test = patch(f"{COMMAND_MODULE}.TaskQueueService", autospec=True)(test)
    test = patch(f"{COMMAND_MODULE}.OpenDataLoaderHybridClient", autospec=True)(test)
    return patch(f"{COMMAND_MODULE}.call_command", autospec=True)(test)


@override_settings(
    OPENDATALOADER_HYBRID_URL="http://localhost:5002",
    OCR_READY_TIMEOUT_SECONDS=600,
    OCR_READY_POLL_SECONDS=5,
)
class RunQueuedTasksCommandTests(SimpleTestCase):
    """ADR 0023 — the scheduled worker: exit fast when idle, wait for OCR only when there's
    work, then hand off to django-tasks-db's batch worker.
    """

    @patch_boundaries
    def test_empty_queue_exits_without_waiting_for_ocr_or_running_worker(
        self, mock_queue_cls, mock_client_cls, mock_call_command
    ) -> None:
        mock_queue_cls.return_value.has_ready_tasks.return_value = False

        call_command("run_queued_tasks")

        mock_client_cls.assert_not_called()
        mock_call_command.assert_not_called()

    @parameterized.expand(
        [
            ("ocr_on_waits_for_ocr", True, True),
            ("ocr_off_skips_wait", False, False),
        ]
    )
    @patch_boundaries
    def test_queued_work_runs_batch_worker(
        self, _name, run_ocr, expect_wait, mock_queue_cls, mock_client_cls, mock_call_command
    ) -> None:
        mock_queue_cls.return_value.has_ready_tasks.return_value = True

        with override_settings(RUN_OCR=run_ocr):
            call_command("run_queued_tasks")

        if expect_wait:
            mock_client_cls.assert_called_once_with("http://localhost:5002")
            mock_client_cls.return_value.wait_until_ready.assert_called_once_with(
                timeout=600, poll_interval=5
            )
        else:
            mock_client_cls.assert_not_called()
        mock_call_command.assert_called_once_with(
            "db_worker", batch=True, startup_delay=False, reload=False
        )

    @override_settings(RUN_OCR=True)
    @patch_boundaries
    def test_ocr_never_ready_fails_the_run_without_running_worker(
        self, mock_queue_cls, mock_client_cls, mock_call_command
    ) -> None:
        mock_queue_cls.return_value.has_ready_tasks.return_value = True
        mock_client_cls.return_value.wait_until_ready.side_effect = HybridServerNotReady("down")

        with self.assertRaises(CommandError):
            call_command("run_queued_tasks")

        mock_call_command.assert_not_called()
