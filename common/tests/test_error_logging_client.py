from unittest.mock import patch

from django.test import SimpleTestCase

from common.error_logging_client import ErrorLoggingClient


class ErrorLoggingClientTests(SimpleTestCase):
    def setUp(self) -> None:
        self.client = ErrorLoggingClient()

    @patch("common.error_logging_client.sentry_sdk.capture_exception", autospec=True)
    def test_report_exception_forwards_to_sentry(self, mock_capture) -> None:
        exc = ValueError("boom")

        self.client.report_exception(exc, tags={"step": "ocr", "remediation_id": "abc"})

        mock_capture.assert_called_once_with(exc, tags={"step": "ocr", "remediation_id": "abc"})

    @patch("common.error_logging_client.sentry_sdk.capture_message", autospec=True)
    def test_report_warning_forwards_to_sentry_at_warning_level(self, mock_capture) -> None:
        self.client.report_warning("something is off")

        mock_capture.assert_called_once_with("something is off", level="warning")

    # Not autospec'd: config.test_settings already replaces `sentry_sdk.init` with a MagicMock.
    @patch("common.error_logging_client.sentry_sdk.init")
    def test_configure_starts_sentry_without_personal_data(self, mock_init) -> None:
        self.client.configure(
            dsn="https://key@sentry.example/1", environment="staging", release="1.2.3"
        )

        _, kwargs = mock_init.call_args
        self.assertEqual(kwargs["dsn"], "https://key@sentry.example/1")
        self.assertEqual(kwargs["environment"], "staging")
        self.assertEqual(kwargs["release"], "1.2.3")
        self.assertFalse(kwargs["send_default_pii"])

    @patch("common.error_logging_client.ignore_logger", autospec=True)
    @patch("common.error_logging_client.sentry_sdk.init")
    def test_configure_ignores_dbbackup_log_records(self, _mock_init, mock_ignore) -> None:
        self.client.configure(dsn="", environment="staging", release="1.2.3")

        mock_ignore.assert_called_once_with("dbbackup")
