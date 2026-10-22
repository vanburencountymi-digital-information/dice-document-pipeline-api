from django.core.exceptions import ImproperlyConfigured
from rest_framework.settings import api_settings
from rest_framework.throttling import SimpleRateThrottle

from common.throttle_scopes import ThrottleScope


class _RateParser(SimpleRateThrottle):
    """Only here so rate text like `100/day` is read by DRF's own parser, not by hand."""

    scope = "rate_parser"  # DRF wants a scope; this one is never looked up
    rate = "1/second"  # makes DRF skip the settings lookup in __init__


def parse_rate(rate: str) -> tuple[int, int]:
    """Takes: rate text such as `100/day`. Returns: (requests allowed, period in seconds).

    Raises: ImproperlyConfigured if the text can't be read.
    """
    requests, seconds = _RateParser().parse_rate(rate)
    # DRF returns (None, None) when there is no rate. Never let that slip through as "no limit".
    if requests is None or seconds is None:
        raise ImproperlyConfigured(f"Can't read the rate {rate!r}; expected e.g. '100/day'.")
    return requests, seconds


def rate_for(scope: ThrottleScope) -> tuple[int, int]:
    """Takes: a throttle scope. Returns: its (requests allowed, period in seconds) from
    `REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]`. The one route from settings to a rate."""
    return parse_rate(str(api_settings.DEFAULT_THROTTLE_RATES[scope]))
