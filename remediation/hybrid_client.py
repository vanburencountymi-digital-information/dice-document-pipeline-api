import time

import httpx


class HybridServerNotReady(Exception):
    """Raised when the OpenDataLoader hybrid (OCR) server doesn't come up within the timeout."""


class OpenDataLoaderHybridClient:
    """Readiness check for the OpenDataLoader hybrid (OCR) server (ADR 0023). Not part of the
    `adapters/` hierarchy for the same reason as `WebhookClient` — it isn't a pipeline-stage
    adapter; `OpenDataLoaderAdapter` is what actually sends documents to this server.

    The server only answers `/health` once its startup has finished loading the OCR models,
    so a 200 there means it's ready for real work, not just that the process is up.
    """

    def __init__(self, base_url: str, request_timeout: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.request_timeout = request_timeout

    def is_ready(self) -> bool:
        try:
            response = httpx.get(f"{self.base_url}/health", timeout=self.request_timeout)
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    def wait_until_ready(self, *, timeout: float, poll_interval: float) -> None:
        deadline = time.monotonic() + timeout
        while not self.is_ready():
            if time.monotonic() >= deadline:
                raise HybridServerNotReady(
                    f"OCR server at {self.base_url} not ready after {timeout:.0f}s"
                )
            time.sleep(poll_interval)
