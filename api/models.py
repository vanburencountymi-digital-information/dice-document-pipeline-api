from django.db import models


class ThrottleCounter(models.Model):
    """How many times a key (e.g. an IP address) was seen in its current time window.

    One row per key, updated in place with an atomic increment (ADR 0031). Expired rows are
    cleaned up whenever a new row is made.
    """

    key = models.CharField(max_length=100, unique=True)
    count = models.PositiveIntegerField()
    window_ends_at = models.DateTimeField(db_index=True)

    def __str__(self) -> str:
        return f"{self.key}: {self.count} until {self.window_ends_at:%Y-%m-%d %H:%M:%S}"
