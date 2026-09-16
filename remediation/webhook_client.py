import hashlib
import hmac
import json
from typing import Any

import httpx


class WebhookDeliveryError(Exception):
    """Raised on any delivery failure — connection error, timeout, or non-2xx response."""


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
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise WebhookDeliveryError(f"webhook delivery to {callback_url} failed: {exc}") from exc
