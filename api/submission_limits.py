"""An account's submission limit (ADR 0030, ADR 0031).

Not DRF throttles: the submit view hands `check` to `RemediationService`, which calls it only
when a new job is about to be made, so a deduped resubmit is never counted or turned away.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from django.utils import timezone
from rest_framework.exceptions import Throttled

from accounts.models import ServiceAccount
from api.rates import parse_rate
from api.serializers import SubmissionLimitSerializer
from common.clock import seconds_until
from common.throttle_scopes import LimitKind
from remediation.services import RemediationService, SubmissionUsage

SUBMISSION_LIMIT_MESSAGE = "Submission limit reached."


class SubmissionLimitReached(Throttled):
    """The 429 for a used-up submission limit, with the JSON body in `SubmissionLimitSerializer`.

    DRF's `Throttled` only builds a text message when it is given a wait, so the body is set
    afterwards. `Retry-After` still comes from `wait`.
    """

    def __init__(self, wait: int, body: dict[str, Any]) -> None:
        """Takes: seconds until the limit frees up, and the response body."""
        super().__init__(wait=wait)
        self.detail = body


@dataclass(frozen=True)
class SubmissionLimitState:
    """How much of an account's submission limit is used, for the response headers."""

    limit: int
    remaining: int
    reset_seconds: int

    def as_headers(self) -> dict[str, str]:
        """Returns: the `X-RateLimit-*` headers for this state."""
        return {
            "X-RateLimit-Limit": str(self.limit),
            "X-RateLimit-Remaining": str(self.remaining),
            "X-RateLimit-Reset": str(self.reset_seconds),
        }


class SubmissionLimitLike(Protocol):
    """What the view needs from an account's limit, whether it has one or is unlimited."""

    def check(self) -> None:
        """Raises: `Throttled` (429) if the account is over its limit."""

    def headers(self) -> dict[str, str]:
        """Returns: the `X-RateLimit-*` headers to send (none for an unlimited account)."""


class SubmissionLimit:
    """A service account's submission limit, e.g. `100/day` (ADR 0030).

    Counts the account's actual jobs made in the last period, rather than keeping a separate
    counter, so the count can never drift from what was really submitted. It is a sliding
    window: a job stops counting exactly one period after it was made.

    Not a DRF throttle class: the submit view hands `check` to the service, which calls it
    only when a new job is about to be made, so a deduped resubmit never counts.
    """

    def __init__(self, service_account: ServiceAccount, rate: str) -> None:
        """Takes: the account and its `ServiceAccount.rate_limit` text (e.g. `100/day`)."""
        self.service_account = service_account
        self.num_requests, self.duration = parse_rate(rate)

    def headers(self) -> dict[str, str]:
        """Returns: the `X-RateLimit-*` headers for the account's current usage."""
        usage = RemediationService().recent_submissions(self.service_account, self.duration)
        return self._state_from(usage).as_headers()

    def _state_from(self, usage: SubmissionUsage) -> SubmissionLimitState:
        """Takes: the account's recent usage. Returns: the limit, what is left, and when it resets.

        The one place these are worked out, so the headers and the 429 body always agree.
        """
        return SubmissionLimitState(
            limit=self.num_requests,
            remaining=max(0, self.num_requests - usage.count),
            reset_seconds=self._seconds_until_oldest_expires(usage.oldest),
        )

    def check(self) -> None:
        """Locks the account's row until the surrounding transaction ends, then checks the limit.

        Because of the lock, two requests for the same account can't both see room for one
        more and both make a job. `RemediationService.get_or_create_from_upload` calls this
        inside its transaction, just before it makes the job.

        Raises: `Throttled` (429) with the body in `SubmissionLimitSerializer` if the limit is used
        up. A limit of 0 is always used up.
        """
        usage = RemediationService().recent_submissions(
            self.service_account, self.duration, lock=True
        )
        if usage.count < self.num_requests:
            return
        state = self._state_from(usage)
        wait = state.reset_seconds
        body = SubmissionLimitSerializer(
            {
                "detail": SUBMISSION_LIMIT_MESSAGE,
                "limit": LimitKind.RATE,
                "retry_after": wait,
                "remaining": state.remaining,
                "resets_at": timezone.now() + timedelta(seconds=wait),
            }
        )
        raise SubmissionLimitReached(wait=wait, body=dict(body.data))

    def _seconds_until_oldest_expires(self, oldest: datetime | None) -> int:
        """Takes: when the oldest counted job was made (None if none). Returns: seconds until
        it stops counting, or 0 if there is none. With a limit of 0 there are no jobs to wait
        out but the account really is blocked, so it is a whole period, not 0."""
        if oldest is None:
            return self.duration if self.num_requests == 0 else 0
        return seconds_until(oldest + timedelta(seconds=self.duration))


class NoSubmissionLimit:
    """Stands in for `SubmissionLimit` on an unlimited account (ADR 0030): never throttles
    and sends no `X-RateLimit-*` headers."""

    def check(self) -> None:
        """Does nothing: an unlimited account is never throttled."""

    def headers(self) -> dict[str, str]:
        """Returns: no headers."""
        return {}


def limit_for(service_account: ServiceAccount) -> SubmissionLimitLike:
    """Picks the submission throttle for an account.

    Takes: the account. Returns: its throttle, or `NoSubmissionLimit` only if it is unlimited.
    Raises: `IncompleteLimitsError` if the limits are half-filled, which saving refuses. The
    caller must deny the request, never treat it as unlimited.
    """
    rate = service_account.rate_limit
    return NoSubmissionLimit() if rate is None else SubmissionLimit(service_account, rate)
