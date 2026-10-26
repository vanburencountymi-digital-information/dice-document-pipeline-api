import hmac
import logging
from collections.abc import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.urls import reverse

LOGGER = logging.getLogger(__name__)

# Django's name for the `X-Origin-Secret` header. The Cloudflare Worker that adds the header
# (`deploy/terraform/prod/worker/index.js`) must use that same name (ADR 0041).
HEADER_META_KEY = "HTTP_X_ORIGIN_SECRET"


class OriginLockMiddleware:
    """Rejects any request that doesn't carry the secret Cloudflare adds, so the app's raw
    `run.app` address can't be used to get around Cloudflare (ADR 0036). Does nothing when
    `ORIGIN_LOCK_SECRET` is empty. Must be first in `MIDDLEWARE`, so nothing else runs (or
    answers, e.g. with a redirect) for a rejected request. `GET`/`HEAD` of the `ping` URL is let
    through, because Cloud Run's probes call the app directly and carry no secret (ADR 0037).
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    @staticmethod
    def _is_probe(request: HttpRequest) -> bool:
        """Takes the request. Returns True for a `GET`/`HEAD` of the `ping` URL, which Cloud Run's
        probes send without the secret (ADR 0037)."""
        return request.method in ("GET", "HEAD") and request.path == reverse("ping")

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Takes the incoming `HttpRequest`. Returns a 403 `HttpResponse` if the lock is on and
        the secret header is missing or wrong (except `GET`/`HEAD` of the `ping` URL), otherwise
        whatever the rest of the app returns.
        """
        secret = settings.ORIGIN_LOCK_SECRET
        if not secret or self._is_probe(request):
            return self.get_response(request)
        if not hmac.compare_digest(request.META.get(HEADER_META_KEY, "").encode(), secret.encode()):
            # Method and path only: never the header value, which is a near-miss of the secret.
            # The path is logged with %r because callers control it and can put newlines in it.
            LOGGER.warning(
                "Origin lock rejected %s %r: X-Origin-Secret missing or wrong",
                request.method,
                request.path,
            )
            return HttpResponseForbidden("Forbidden")
        return self.get_response(request)
