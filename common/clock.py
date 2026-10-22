import math
from datetime import datetime

from django.utils import timezone


def seconds_until(moment: datetime) -> int:
    """Takes: a time. Returns: whole seconds from now until then (rounded up), never below 0."""
    return max(0, math.ceil((moment - timezone.now()).total_seconds()))
