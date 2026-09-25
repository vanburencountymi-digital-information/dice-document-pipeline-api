from unittest.mock import patch

import httpx
from django.test import SimpleTestCase
from parameterized import parameterized

from remediation.hybrid_client import HybridServerNotReady, OpenDataLoaderHybridClient

HEALTH_URL = "http://ocr:5002/health"


class OpenDataLoaderHybridClientTests(SimpleTestCase):
    def setUp(self) -> None:
        self.client = OpenDataLoaderHybridClient("http://ocr:5002/")

    @patch("remediation.hybrid_client.time", autospec=True)
    @patch("remediation.hybrid_client.httpx.get", autospec=True)
    def test_returns_once_health_answers_200(self, mock_get, mock_time) -> None:
        mock_get.return_value = httpx.Response(200)
        mock_time.monotonic.return_value = 0

        self.client.wait_until_ready(timeout=60, poll_interval=5)

        mock_get.assert_called_once_with(HEALTH_URL, timeout=5.0)
        mock_time.sleep.assert_not_called()

    @parameterized.expand(
        [
            ("connection_error", httpx.ConnectError("refused")),
            ("not_ok_status", httpx.Response(503)),
        ]
    )
    @patch("remediation.hybrid_client.time", autospec=True)
    @patch("remediation.hybrid_client.httpx.get", autospec=True)
    def test_keeps_polling_until_ready(self, _name, first_result, mock_get, mock_time) -> None:
        mock_get.side_effect = [first_result, httpx.Response(200)]
        mock_time.monotonic.return_value = 0

        self.client.wait_until_ready(timeout=60, poll_interval=5)

        self.assertEqual(mock_get.call_count, 2)
        mock_time.sleep.assert_called_once_with(5)

    @patch("remediation.hybrid_client.time", autospec=True)
    @patch("remediation.hybrid_client.httpx.get", autospec=True)
    def test_raises_when_not_ready_before_timeout(self, mock_get, mock_time) -> None:
        mock_get.side_effect = httpx.ConnectError("refused")
        mock_time.monotonic.side_effect = [0, 30, 61]

        with self.assertRaises(HybridServerNotReady):
            self.client.wait_until_ready(timeout=60, poll_interval=5)

        self.assertEqual(mock_time.sleep.call_count, 1)
