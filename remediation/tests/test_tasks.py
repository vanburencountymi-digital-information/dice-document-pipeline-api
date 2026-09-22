from __future__ import annotations

import tempfile
from unittest.mock import patch

from django.tasks import TaskResultStatus
from django.test import TestCase, override_settings
from django.utils import timezone
from parameterized import parameterized

from remediation.adapters.base import AdapterError, FailedRule, VerificationOutcome
from remediation.adapters.verification.severity import Severity
from remediation.models import Remediation, RemediationArtifact
from remediation.tasks import (
    MAX_WEBHOOK_ATTEMPTS,
    process_remediation,
    send_webhook_notification,
)
from remediation.tests.factories import (
    RemediationArtifactFactory,
    RemediationCallbackFactory,
    RemediationFactory,
    VerificationResultFactory,
)
from remediation.tests.helpers import fake_adapter_output, write_fake_pdf
from remediation.webhook_client import WebhookDeliveryError


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
        self.assertEqual(remediation.final_output_uri, remediation.source_pdf_uri)
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
        remediation = RemediationFactory(with_stored_file=True)

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
        remediation = RemediationFactory(with_stored_file=True)

        result = process_remediation.enqueue(str(remediation.id))

        remediation.refresh_from_db()
        self.assertEqual(result.status, TaskResultStatus.SUCCESSFUL)
        self.assertEqual(remediation.status, Remediation.JobStatus.FAILED)
        self.assertEqual(
            remediation.error,
            "postcheck: 1 rules failed, 1 checks\n"
            "  - CRITICAL     7.1 StructTreeRoot missing (1 checks)",
        )
        self.assertEqual(remediation.final_output_uri, remediation.source_pdf_uri)
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
        remediation = RemediationFactory(with_stored_file=True)
        mock_ocr_cls.return_value.extract.side_effect = fake_adapter_output(filename="test.pdf")

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
    def test_redelivered_task_resumes_without_rerunning_completed_steps(
        self, mock_vera_cls, mock_ocr_cls
    ) -> None:
        """ADR 0019 — the state a worker crash after OCR leaves behind: still `RUNNING`,
        precheck and OCR already recorded. Only postcheck should actually run.
        """
        remediation = RemediationFactory(
            with_stored_file=True, status=Remediation.JobStatus.RUNNING
        )
        RemediationArtifactFactory(
            remediation=remediation,
            step=RemediationArtifact.Step.PRECHECK,
            status=RemediationArtifact.StepStatus.COMPLETED,
            output_uri=remediation.source_pdf_uri,
        )
        VerificationResultFactory(
            remediation=remediation, step=RemediationArtifact.Step.PRECHECK, is_compliant=False
        )
        ocr_output_uri = f"remediations/{remediation.id}/ocr/test.pdf"
        write_fake_pdf(ocr_output_uri)
        RemediationArtifactFactory(
            remediation=remediation,
            step=RemediationArtifact.Step.OCR,
            status=RemediationArtifact.StepStatus.COMPLETED,
            output_uri=ocr_output_uri,
        )
        mock_vera_cls.return_value.validate.return_value = VerificationOutcome(
            is_compliant=True, failed_rules=[], verapdf_version="1.30.2"
        )

        result = process_remediation.enqueue(str(remediation.id))

        remediation.refresh_from_db()
        self.assertEqual(result.status, TaskResultStatus.SUCCESSFUL)
        self.assertEqual(remediation.status, Remediation.JobStatus.COMPLETE)
        self.assertEqual(remediation.final_output_uri, ocr_output_uri)
        mock_ocr_cls.return_value.extract.assert_not_called()
        mock_vera_cls.return_value.validate.assert_called_once()
        self.assertEqual(
            set(
                remediation.artifacts.filter(
                    status=RemediationArtifact.StepStatus.COMPLETED
                ).values_list("step", flat=True)
            ),
            {
                RemediationArtifact.Step.PRECHECK,
                RemediationArtifact.Step.OCR,
                RemediationArtifact.Step.POSTCHECK,
            },
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
        remediation = RemediationFactory(with_stored_file=True)

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
        remediation = RemediationFactory(with_stored_file=True)

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
        remediation = RemediationFactory(with_stored_file=True)

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
        remediation = RemediationFactory(with_stored_file=True)

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


@override_settings(
    TASKS={"default": {"BACKEND": "django.tasks.backends.immediate.ImmediateBackend"}},
    MEDIA_ROOT=tempfile.mkdtemp(),
    RUN_PRECHECK=False,
    RUN_OCR=False,
    RUN_FONT_REPAIR=False,
    RUN_FINALIZE_METADATA=False,
    RUN_LINK_TAG=False,
    RUN_ALT_TEXT=False,
    RUN_SCORING=False,
    RUN_POSTCHECK=False,
)
class ProcessRemediationWebhookTests(TestCase):
    """ADR 0015 — `process_remediation`'s `finally` block enqueues `send_webhook_notification`
    for every `RemediationCallback` registered on the attempt, on every terminal path.
    """

    @patch("remediation.tasks.send_webhook_notification", autospec=True, spec_set=True)
    def test_enqueues_every_registered_callback_on_success(self, mock_send) -> None:
        remediation = RemediationFactory()
        callback_a = RemediationCallbackFactory(remediation=remediation)
        callback_b = RemediationCallbackFactory(remediation=remediation)

        process_remediation.enqueue(str(remediation.id))

        enqueued_ids = {call.args[0] for call in mock_send.enqueue.call_args_list}
        self.assertEqual(enqueued_ids, {str(callback_a.id), str(callback_b.id)})

    @parameterized.expand(
        [
            ("complete", Remediation.JobStatus.COMPLETE),
            ("failed", Remediation.JobStatus.FAILED),
        ]
    )
    @patch("remediation.tasks.send_webhook_notification", autospec=True, spec_set=True)
    def test_redelivered_task_for_finished_remediation_is_a_noop(
        self, _name, status, mock_send
    ) -> None:
        """ADR 0019 — no steps re-run, timestamps untouched, no second round of webhooks."""
        remediation = RemediationFactory(status=status, completed_at=timezone.now())
        RemediationCallbackFactory(remediation=remediation)
        completed_at = remediation.completed_at

        result = process_remediation.enqueue(str(remediation.id))

        remediation.refresh_from_db()
        self.assertEqual(result.status, TaskResultStatus.SUCCESSFUL)
        self.assertEqual(remediation.status, status)
        self.assertEqual(remediation.completed_at, completed_at)
        self.assertIsNone(remediation.started_at)
        self.assertFalse(remediation.artifacts.exists())
        mock_send.enqueue.assert_not_called()

    @patch("remediation.tasks.send_webhook_notification", autospec=True, spec_set=True)
    def test_does_not_enqueue_when_no_callbacks_registered(self, mock_send) -> None:
        remediation = RemediationFactory()

        process_remediation.enqueue(str(remediation.id))

        mock_send.enqueue.assert_not_called()

    @override_settings(RUN_PRECHECK=True, RUN_POSTCHECK=True)
    @patch("remediation.services.VeraPDFAdapter", autospec=True)
    @patch("remediation.tasks.send_webhook_notification", autospec=True, spec_set=True)
    def test_enqueues_callback_when_already_compliant(self, mock_send, mock_adapter_cls) -> None:
        mock_adapter_cls.return_value.validate.return_value = VerificationOutcome(
            is_compliant=True, failed_rules=[], verapdf_version="1.30.2"
        )
        remediation = RemediationFactory(with_stored_file=True)
        callback = RemediationCallbackFactory(remediation=remediation)

        process_remediation.enqueue(str(remediation.id))

        mock_send.enqueue.assert_called_once_with(str(callback.id))

    @override_settings(RUN_PRECHECK=True, RUN_POSTCHECK=True)
    @patch("remediation.services.VeraPDFAdapter", autospec=True)
    @patch("remediation.tasks.send_webhook_notification", autospec=True, spec_set=True)
    def test_enqueues_callback_when_not_compliant(self, mock_send, mock_adapter_cls) -> None:
        mock_adapter_cls.return_value.validate.side_effect = [
            VerificationOutcome(is_compliant=False, failed_rules=[], verapdf_version="1.30.2"),
            VerificationOutcome(is_compliant=False, failed_rules=[], verapdf_version="1.30.2"),
        ]
        remediation = RemediationFactory(with_stored_file=True)
        callback = RemediationCallbackFactory(remediation=remediation)

        process_remediation.enqueue(str(remediation.id))

        mock_send.enqueue.assert_called_once_with(str(callback.id))

    @override_settings(RUN_PRECHECK=True)
    @patch("remediation.tasks.PrecheckService.run", side_effect=RuntimeError("db exploded"))
    @patch("remediation.tasks.send_webhook_notification", autospec=True, spec_set=True)
    def test_enqueues_callback_even_when_the_task_itself_fails(self, mock_send, mock_run) -> None:
        """The generic `except Exception` branch re-raises after `mark_failed` — `finally`
        still has to fire and enqueue every registered callback before that re-raise
        propagates out of the task.
        """
        remediation = RemediationFactory()
        callback = RemediationCallbackFactory(remediation=remediation)

        result = process_remediation.enqueue(str(remediation.id))

        self.assertEqual(result.status, TaskResultStatus.FAILED)
        mock_send.enqueue.assert_called_once_with(str(callback.id))


@override_settings(
    TASKS={"default": {"BACKEND": "django.tasks.backends.immediate.ImmediateBackend"}},
    MEDIA_ROOT=tempfile.mkdtemp(),
)
class SendWebhookNotificationTaskTests(TestCase):
    @patch("remediation.tasks.WebhookClient", autospec=True)
    def test_builds_payload_with_download_url_when_final_output_uri_set(
        self, mock_client_cls
    ) -> None:
        remediation = RemediationFactory(
            status=Remediation.JobStatus.COMPLETE, final_output_uri="remediations/final.pdf"
        )
        callback = RemediationCallbackFactory(remediation=remediation)

        send_webhook_notification.enqueue(str(callback.id))

        mock_client_cls.return_value.notify.assert_called_once()
        call = mock_client_cls.return_value.notify.call_args
        self.assertEqual(call.args[0], callback.callback_url)
        self.assertEqual(call.kwargs["secret"], remediation.service_account.webhook_secret)
        payload = call.kwargs["payload"]
        self.assertEqual(payload["remediation_id"], str(remediation.id))
        self.assertEqual(payload["document_id"], remediation.content_hash)
        self.assertEqual(payload["status"], Remediation.JobStatus.COMPLETE)
        self.assertIn("download_url", payload)
        self.assertNotIn("error", payload)

    @patch("remediation.tasks.WebhookClient", autospec=True)
    def test_builds_payload_with_error_when_failed(self, mock_client_cls) -> None:
        remediation = RemediationFactory(
            status=Remediation.JobStatus.FAILED, error="postcheck: not compliant"
        )
        callback = RemediationCallbackFactory(remediation=remediation)

        send_webhook_notification.enqueue(str(callback.id))

        payload = mock_client_cls.return_value.notify.call_args.kwargs["payload"]
        self.assertEqual(payload["error"], "postcheck: not compliant")
        self.assertNotIn("download_url", payload)

    @patch("remediation.tasks.WebhookClient", autospec=True)
    def test_no_download_url_when_final_output_uri_blank(self, mock_client_cls) -> None:
        remediation = RemediationFactory(status=Remediation.JobStatus.QUEUED)
        callback = RemediationCallbackFactory(remediation=remediation)

        send_webhook_notification.enqueue(str(callback.id))

        payload = mock_client_cls.return_value.notify.call_args.kwargs["payload"]
        self.assertNotIn("download_url", payload)

    @patch("remediation.tasks.WebhookClient", autospec=True)
    def test_delivery_failure_retries_until_max_attempts_then_gives_up(
        self, mock_client_cls
    ) -> None:
        """ImmediateBackend ignores `run_after` and executes a re-enqueued attempt
        synchronously right here (see the caveat in `send_webhook_notification`'s
        docstring), so one top-level call exhausts every attempt before returning —
        `WebhookClient.notify` ends up called once per attempt, `MAX_WEBHOOK_ATTEMPTS`
        times in total.
        """
        mock_client_cls.return_value.notify.side_effect = WebhookDeliveryError("boom")
        remediation = RemediationFactory(status=Remediation.JobStatus.COMPLETE)
        callback = RemediationCallbackFactory(remediation=remediation)

        send_webhook_notification.enqueue(str(callback.id))

        self.assertEqual(mock_client_cls.return_value.notify.call_count, MAX_WEBHOOK_ATTEMPTS)

    @patch("remediation.tasks.WebhookClient", autospec=True)
    def test_raises_once_max_attempts_reached_instead_of_reenqueueing(
        self, mock_client_cls
    ) -> None:
        mock_client_cls.return_value.notify.side_effect = WebhookDeliveryError("boom")
        remediation = RemediationFactory(status=Remediation.JobStatus.COMPLETE)
        callback = RemediationCallbackFactory(remediation=remediation)

        result = send_webhook_notification.enqueue(str(callback.id), attempt=5)

        self.assertEqual(result.status, TaskResultStatus.FAILED)
