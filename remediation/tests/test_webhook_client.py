from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import patch

import httpx
from django.test import SimpleTestCase

from remediation.webhook_client import WebhookClient, WebhookDeliveryError


class WebhookClientTests(SimpleTestCase):
    def setUp(self) -> None:
        self.client = WebhookClient()

    @patch("remediation.webhook_client.httpx.post", autospec=True)
    def test_notify_sends_signed_payload(self, mock_post) -> None:
        mock_post.return_value = httpx.Response(200, request=httpx.Request("POST", "https://x"))

        self.client.notify(
            "https://example.com/webhook",
            secret="s3cr3t",
            payload={"remediation_id": "abc", "status": "complete"},
        )

        mock_post.assert_called_once()
        call = mock_post.call_args
        self.assertEqual(call.args[0], "https://example.com/webhook")
        body = call.kwargs["content"]
        self.assertEqual(json.loads(body), {"remediation_id": "abc", "status": "complete"})
        expected_signature = hmac.new(b"s3cr3t", body, hashlib.sha256).hexdigest()
        self.assertEqual(
            call.kwargs["headers"]["X-Webhook-Signature"], f"sha256={expected_signature}"
        )
        self.assertEqual(call.kwargs["headers"]["Content-Type"], "application/json")

    @patch("remediation.webhook_client.httpx.post", autospec=True)
    def test_notify_raises_webhook_delivery_error_on_non_2xx(self, mock_post) -> None:
        request = httpx.Request("POST", "https://example.com/webhook")
        mock_post.return_value = httpx.Response(500, request=request)

        with self.assertRaises(WebhookDeliveryError):
            self.client.notify("https://example.com/webhook", secret="s3cr3t", payload={})

    @patch("remediation.webhook_client.httpx.post", autospec=True)
    def test_notify_raises_webhook_delivery_error_on_connection_error(self, mock_post) -> None:
        mock_post.side_effect = httpx.ConnectError("boom")

        with self.assertRaises(WebhookDeliveryError):
            self.client.notify("https://example.com/webhook", secret="s3cr3t", payload={})

    @patch("remediation.webhook_client.httpx.post", autospec=True)
    def test_notify_raises_webhook_delivery_error_on_timeout(self, mock_post) -> None:
        mock_post.side_effect = httpx.TimeoutException("boom")

        with self.assertRaises(WebhookDeliveryError):
            self.client.notify("https://example.com/webhook", secret="s3cr3t", payload={})
