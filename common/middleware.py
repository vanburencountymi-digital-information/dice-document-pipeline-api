import hmac
from collections.abc import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden

HEADER_META_KEY = "HTTP_X_ORIGIN_SECRET"


class OriginLockMiddleware:
    """Rejects any request that doesn't carry the secret Cloudflare adds, so the app's raw
    `run.app` address can't be used to get around Cloudflare (ADR 0036). Does nothing when
    `ORIGIN_LOCK_SECRET` is empty. Must be first in `MIDDLEWARE`, so nothing else runs (or
    answers, e.g. with a redirect) for a rejected request.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        secret = settings.ORIGIN_LOCK_SECRET
        if secret and not hmac.compare_digest(
            request.META.get(HEADER_META_KEY, "").encode(), secret.encode()
        ):
            return HttpResponseForbidden("Forbidden")
        return self.get_response(request)
