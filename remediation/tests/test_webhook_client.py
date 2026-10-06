from __future__ import annotations

import hashlib
import hmac
import json
import socket
from unittest.mock import patch

import httpx
from django.test import SimpleTestCase, override_settings
from parameterized import parameterized

from remediation.webhook_client import (
    UnsafeWebhookURLError,
    WebhookClient,
    WebhookDeliveryError,
    check_webhook_url,
)


class WebhookClientTests(SimpleTestCase):
    def setUp(self) -> None:
        self.client = WebhookClient()

    @patch("remediation.webhook_client.httpx.post", autospec=True)
    def test_notify_sends_signed_payload(self, mock_post) -> None:
        mock_post.return_value = httpx.Response(200, request=httpx.Request("POST", "https://x"))

        self.client.notify(
            "https://example.com/webhook",
            secret="s3cr3t",
            payload={"remediation_id": "abc", "status": "compliant"},
        )

        mock_post.assert_called_once()
        call = mock_post.call_args
        self.assertEqual(call.args[0], "https://example.com/webhook")
        body = call.kwargs["content"]
        self.assertEqual(json.loads(body), {"remediation_id": "abc", "status": "compliant"})
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


def _resolves_to(address: str):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    return patch(
        "remediation.webhook_client.socket.getaddrinfo",
        autospec=True,
        return_value=[(family, socket.SOCK_STREAM, 6, "", (address, 443))],
    )


@override_settings(WEBHOOK_ALLOW_PRIVATE_URLS=False)
class CheckWebhookUrlTests(SimpleTestCase):
    def test_accepts_https_url_on_a_public_address(self) -> None:
        with _resolves_to("93.184.216.34"):
            check_webhook_url("https://example.com/webhook")

    @parameterized.expand(
        [
            ("metadata_server", "169.254.169.254"),
            ("loopback", "127.0.0.1"),
            ("private_10", "10.0.0.5"),
            ("private_192", "192.168.1.10"),
            ("ipv6_loopback", "::1"),
            ("ipv4_mapped_private", "::ffff:10.0.0.5"),
        ]
    )
    def test_rejects_a_host_that_points_to_a_non_public_address(self, _name, address) -> None:
        with _resolves_to(address), self.assertRaises(UnsafeWebhookURLError):
            check_webhook_url("https://sneaky.example.com/webhook")

    def test_rejects_when_any_address_is_non_public(self) -> None:
        mixed = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443)),
        ]
        with (
            patch("remediation.webhook_client.socket.getaddrinfo", return_value=mixed),
            self.assertRaises(UnsafeWebhookURLError),
        ):
            check_webhook_url("https://example.com/webhook")

    @parameterized.expand([("http", "http://example.com/webhook"), ("ftp", "ftp://example.com/x")])
    def test_rejects_anything_but_https(self, _name, url) -> None:
        with self.assertRaises(UnsafeWebhookURLError):
            check_webhook_url(url)

    @patch("remediation.webhook_client.socket.getaddrinfo", side_effect=socket.gaierror)
    def test_rejects_an_unresolvable_host(self, _mock) -> None:
        with self.assertRaises(UnsafeWebhookURLError):
            check_webhook_url("https://nope.invalid/webhook")

    @override_settings(WEBHOOK_ALLOW_PRIVATE_URLS=True)
    def test_private_urls_and_http_are_allowed_when_the_setting_is_on(self) -> None:
        check_webhook_url("http://localhost:9000/webhook")

    @patch("remediation.webhook_client.httpx.post", autospec=True)
    def test_notify_refuses_to_send_to_a_non_public_address(self, mock_post) -> None:
        with _resolves_to("169.254.169.254"), self.assertRaises(WebhookDeliveryError):
            WebhookClient().notify("https://example.com/webhook", secret="s", payload={})

        mock_post.assert_not_called()

    @patch("remediation.webhook_client.httpx.post", autospec=True)
    def test_notify_does_not_follow_redirects(self, mock_post) -> None:
        mock_post.return_value = httpx.Response(200, request=httpx.Request("POST", "https://x"))

        with _resolves_to("93.184.216.34"):
            WebhookClient().notify("https://example.com/webhook", secret="s", payload={})

        self.assertFalse(mock_post.call_args.kwargs["follow_redirects"])
