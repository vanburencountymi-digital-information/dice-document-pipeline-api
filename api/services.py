from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from api.models import ThrottleCounter
from common.clock import seconds_until

# A round is only lost when another request made progress on the same key (it created the row,
# or started a new window first), so a handful of rounds is far more than real contention needs.
MAX_HIT_ATTEMPTS = 5
# Expired rows are left in place until the next row is made, then removed once this old.
EXPIRED_ROW_GRACE = timedelta(hours=1)


@dataclass(frozen=True)
class CounterReading:
    """A key's current use: how many times it was hit in this window (`count`), and the seconds
    until the window ends (`seconds_left`; both 0 if the key has no live window)."""

    count: int
    seconds_left: int


class ThrottleCounterService:
    """Keeps exact per-key counts for the IP-based limits, across all app instances (ADR 0031)."""

    def hit(self, key: str, window_seconds: int) -> CounterReading:
        """Counts one more use of `key`, starting a new window if the last one ended.

        Every step is a single database statement, so requests arriving at the same moment
        can't overwrite each other's counts. For example, with 3 requests at once on a new key:
        all 3 try to update (no row yet), all 3 try to create, one wins and the other 2 hit the
        unique key, go round again and add 1 each. The count ends at 3.

        Takes: the key (max 100 characters) and the window length in seconds.
        Returns: the count including this hit (it can include a few hits that arrived just
        after, never fewer) and the seconds until the window ends.
        Raises: RuntimeError if the row keeps changing under us (not expected).
        """
        now = timezone.now()
        live = ThrottleCounter.objects.filter(key=key, window_ends_at__gt=now)
        expired = ThrottleCounter.objects.filter(key=key, window_ends_at__lte=now)
        ends_at = now + timedelta(seconds=window_seconds)
        for _ in range(MAX_HIT_ATTEMPTS):
            if live.update(count=F("count") + 1):
                return self.read(key)
            # Only one request can win this: it moves the end into the future, so the others'
            # filters no longer match and they add 1 on the next loop.
            if expired.update(count=1, window_ends_at=ends_at):
                return self.read(key)
            try:
                # Own savepoint, so a clash doesn't spoil the caller's transaction (without it,
                # the IntegrityError would leave any enclosing transaction unusable and the
                # next loop's queries would fail).
                with transaction.atomic():
                    ThrottleCounter.objects.create(key=key, count=1, window_ends_at=ends_at)
            except IntegrityError:
                continue
            self._remove_old_rows(now)
            return self.read(key)
        raise RuntimeError(f"could not count a hit for {key!r}")

    def read(self, key: str) -> CounterReading:
        """Takes: a key. Returns: its current count and seconds left, or zeros if none."""
        now = timezone.now()
        row = (
            ThrottleCounter.objects.filter(key=key, window_ends_at__gt=now)
            .values("count", "window_ends_at")
            .first()
        )
        if row is None:
            return CounterReading(count=0, seconds_left=0)
        return CounterReading(count=row["count"], seconds_left=seconds_until(row["window_ends_at"]))

    def _remove_old_rows(self, now: datetime) -> None:
        """Takes: the current time. Removes counters that ended more than an hour ago."""
        ThrottleCounter.objects.filter(window_ends_at__lt=now - EXPIRED_ROW_GRACE).delete()
