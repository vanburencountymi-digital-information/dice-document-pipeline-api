from __future__ import annotations

from typing import Any

from knox.auth import TokenAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.request import Request

from api.lockout import FailedAuthLockout


class LockoutTokenAuthentication(TokenAuthentication):
    """Token login that locks an IP address out after too many wrong tokens (ADR 0031).

    Checked before the token is, so guessing can't get through once the limit is hit. A
    request with no token at all isn't counted.
    """

    def authenticate(self, request: Request) -> tuple[Any, Any] | None:
        """Takes: the request. Returns: `(user, token)`, or `None` if no token was sent.

        Raises: `Throttled` if this IP is locked out; `AuthenticationFailed` for a wrong token
        (which also counts against the IP).
        """
        throttle = FailedAuthLockout(request)
        throttle.check()
        try:
            return super().authenticate(request)
        except AuthenticationFailed:
            throttle.record()
            raise
