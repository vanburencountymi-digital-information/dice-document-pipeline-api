from __future__ import annotations

from rest_framework.exceptions import Throttled
from rest_framework.request import Request

from api.client_ip import build_counter_key
from api.rates import rate_for
from api.services import ThrottleCounterService
from common.throttle_scopes import ThrottleScope


class FailedAuthLockout:
    """Locks an IP address out after too many wrong tokens (ADR 0031).

    Used by `LockoutTokenAuthentication` through `check` and `record`. The rate (for example
    `100/hour`) comes from `REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]` (see `rate_for`).
    """

    def __init__(self, request: Request) -> None:
        """Takes: the request being authenticated."""
        self.max_failures, self.window_seconds = rate_for(ThrottleScope.FAILED_AUTH)
        self.counter_key = build_counter_key(ThrottleScope.FAILED_AUTH, request)

    def check(self) -> None:
        """Raises: `Throttled` (429, with `Retry-After`) if this IP has used up its failures."""
        reading = ThrottleCounterService().read(self.counter_key)
        if reading.count >= self.max_failures:
            raise Throttled(wait=reading.seconds_left)

    def record(self) -> None:
        """Counts one wrong token against this IP."""
        ThrottleCounterService().hit(self.counter_key, self.window_seconds)
