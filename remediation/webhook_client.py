import hashlib
import hmac
import ipaddress
import json
import socket
from typing import Any
from urllib.parse import urlsplit

import httpx
from django.conf import settings


class WebhookDeliveryError(Exception):
    """Raised on any delivery failure — connection error, timeout, or non-2xx response."""


class UnsafeWebhookURLError(ValueError):
    """Raised when a `callback_url` could make the server call something internal."""


def check_webhook_url(url: str) -> None:
    """Raises `UnsafeWebhookURLError` unless `url` is an `https` address on the public internet.

    Without this, a caller could set `callback_url` to an internal address (the cloud metadata
    server, the OCR service, the database) and have our server send requests there. Django's
    `URLField` only checks that the text looks like a URL, so it doesn't catch this.

    The hostname is looked up and every address it points to must be public. This runs when
    the URL is submitted and again right before each delivery. A hostname could still be
    changed between that check and the request itself; closing that gap means connecting to the
    checked address directly, which isn't done here.

    `WEBHOOK_ALLOW_PRIVATE_URLS=True` turns this off, for local development only.
    """
    parts = urlsplit(url)
    allow_private = settings.WEBHOOK_ALLOW_PRIVATE_URLS
    if parts.scheme != "https" and not (allow_private and parts.scheme == "http"):
        raise UnsafeWebhookURLError("callback_url must start with https://")
    if not parts.hostname:
        raise UnsafeWebhookURLError("callback_url must include a host name.")
    if allow_private:
        return
    try:
        addresses = socket.getaddrinfo(parts.hostname, parts.port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeWebhookURLError("callback_url's host name could not be found.") from exc
    for *_, sockaddr in addresses:
        ip = ipaddress.ip_address(str(sockaddr[0]).split("%")[0])
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not ip.is_global:
            raise UnsafeWebhookURLError("callback_url must point to a public address.")


class WebhookClient:
    """Signed webhook delivery (ADR 0015). Not part of the `adapters/` hierarchy — it isn't
    a pipeline-stage adapter (no `RemediationArtifact` tracking, no swap-in-a-different-
    implementation shape), so it gets its own small module and its own exception type
    rather than reusing `AdapterError`.
    """

    def __init__(self, timeout: float = 10.0) -> None:
        self.timeout = timeout

    def notify(self, callback_url: str, *, secret: str, payload: dict[str, Any]) -> None:
        """POSTs `payload` as signed JSON. Raises `WebhookDeliveryError` on any failure —
        one attempt; the caller (`send_webhook_notification`) owns retry via re-enqueueing,
        not this method.
        """
        try:
            check_webhook_url(callback_url)
        except UnsafeWebhookURLError as exc:
            raise WebhookDeliveryError(
                f"webhook delivery to {callback_url} refused: {exc}"
            ) from exc
        body = json.dumps(payload).encode()
        signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        try:
            response = httpx.post(
                callback_url,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Webhook-Signature": f"sha256={signature}",
                },
                timeout=self.timeout,
                # A redirect could send us to an internal address after the check above.
                follow_redirects=False,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise WebhookDeliveryError(f"webhook delivery to {callback_url} failed: {exc}") from exc
