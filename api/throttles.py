from __future__ import annotations

from rest_framework.request import Request
from rest_framework.throttling import BaseThrottle

from accounts.models import ServiceAccount
from api.client_ip import build_counter_key
from api.rates import rate_for
from api.services import ThrottleCounterService
from common.throttle_scopes import ThrottleScope


def _is_unlimited_account(request: Request) -> bool:
    """Takes: the request. Returns: True only if a service account made it and is unlimited.

    A half-filled account is not unlimited (ADR 0030); its requests count as usual and the
    view denies them.
    """
    account = ServiceAccount.for_user(request.user)
    return account is not None and account.is_unlimited


class ClientIPThrottle(BaseThrottle):
    """A loose per-IP cap on every endpoint, including the unauthenticated docs (ADR 0031).

    Skipped for unlimited accounts, so ticking "no limits" really means none (e.g. for a
    backfill). The failed-login lockout still applies to everyone.

    A real DRF throttle (it is in `DEFAULT_THROTTLE_CLASSES`), but it counts in the database
    (`ThrottleCounterService`) instead of DRF's cache, so every app instance shares exact counts.
    """

    def __init__(self) -> None:
        """Reads this throttle's rate from `REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]` (DRF makes
        one throttle per request, with no arguments)."""
        self.num_requests, self.duration = rate_for(ThrottleScope.CLIENT_IP)
        # DRF only calls `wait()` after `allow_request` refuses, which sets this; it starts at 0
        # so the attribute always exists.
        self.seconds_left = 0

    def allow_request(self, request: Request, view: object) -> bool:
        """Counts the request and allows it unless this IP has used up its rate.

        Takes: the request and the view (unused). Returns: True to let the request through.
        """
        if _is_unlimited_account(request):
            return True
        key = build_counter_key(ThrottleScope.CLIENT_IP, request)
        reading = ThrottleCounterService().hit(key, self.duration)
        self.seconds_left = reading.seconds_left
        return reading.count <= self.num_requests

    def wait(self) -> float | None:
        """Returns: seconds until this IP's count resets (for `Retry-After`)."""
        return self.seconds_left
