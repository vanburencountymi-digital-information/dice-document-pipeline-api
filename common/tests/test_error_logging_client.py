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
