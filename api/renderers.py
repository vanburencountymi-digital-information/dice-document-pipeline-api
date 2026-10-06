from typing import Any

from rest_framework.renderers import BaseRenderer


class PDFRenderer(BaseRenderer):
    """Lets a request that asks for `Accept: application/pdf` get past content negotiation.

    The download view returns a ready-made `FileResponse`, which DRF never renders, so
    `render` is only a placeholder.
    """

    media_type = "application/pdf"
    format = "pdf"
    charset = None
    render_style = "binary"

    def render(
        self,
        data: Any,
        accepted_media_type: str | None = None,
        renderer_context: Any = None,
    ) -> Any:
        return data
