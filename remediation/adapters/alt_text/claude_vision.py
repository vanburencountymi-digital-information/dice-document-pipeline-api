import base64

import anthropic
from anthropic.types import ImageBlockParam, MessageParam, TextBlockParam

from remediation.adapters.base import AltTextClient, ImageMediaType

DEFAULT_MODEL = "claude-sonnet-5"
MAX_ALT_TEXT_TOKENS = 200


class ClaudeVisionClient(AltTextClient):
    """Wraps the Anthropic API to generate a WCAG-standard alt text description for one
    figure image (ADR 0005). Ported from v1's `_call_claude`, not redesigned.

    `api_key`/`model` are constructor args, not read from settings directly here — same
    pattern `OpenDataLoaderAdapter` uses for `hybrid_url` — so `AltTextService` stays the
    single place that knows about Django settings. The underlying `anthropic.Anthropic`
    client is built lazily on first `describe()` call, not in `__init__`: every pipeline
    step's `Service` is instantiated unconditionally before `RUN_ALT_TEXT` is checked
    (see `tasks.py`), so `__init__` must stay cheap even when this step is disabled.
    """

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self.api_key = api_key or ""
        self.model = model or DEFAULT_MODEL
        self._client: anthropic.Anthropic | None = None

    @property
    def name(self) -> str:
        return "Claude Vision Client"

    def describe(
        self,
        image_bytes: bytes,
        *,
        media_type: ImageMediaType,
        document_title: str,
        page_number: int,
    ) -> str:
        if not self.api_key:
            self.raise_adapter_error("ANTHROPIC_API_KEY is not set")

        context = (
            f'This is page {page_number} of a document titled "{document_title}".'
            if document_title
            else f"This is page {page_number} of a document."
        )
        prompt_text = (
            f"{context}\n\n"
            "Write a concise alt text description for this image suitable for a screen "
            "reader. Keep it under 125 characters. If the image is purely decorative (a "
            "rule, border, or background), respond with exactly: Decorative image"
        )
        content: list[ImageBlockParam | TextBlockParam] = [
            ImageBlockParam(
                type="image",
                source={
                    "type": "base64",
                    "media_type": media_type,
                    "data": base64.standard_b64encode(image_bytes).decode(),
                },
            ),
            TextBlockParam(type="text", text=prompt_text),
        ]

        try:
            response = self._get_client().messages.create(
                model=self.model,
                max_tokens=MAX_ALT_TEXT_TOKENS,
                messages=[MessageParam(role="user", content=content)],
            )
        except Exception as exc:
            self.raise_adapter_error(f"Claude Vision request failed: {exc}")

        block = response.content[0]
        text = getattr(block, "text", "")
        return text.strip()

    def _get_client(self) -> anthropic.Anthropic:
        if self._client is None:
            self._client = anthropic.Anthropic(api_key=self.api_key)
        return self._client
