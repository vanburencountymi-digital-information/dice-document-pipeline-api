from django.conf import settings
from django.test import override_settings

from common.throttle_scopes import ThrottleScope


def with_throttle_rate(scope: ThrottleScope, rate: str):  # type: ignore[no-untyped-def]
    """Takes: a throttle scope and a rate such as `2/hour`. Returns: an `override_settings` that
    sets that scope's limit to it (as a decorator on a test or a test class)."""
    rates = {**settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"], scope: rate}
    return override_settings(
        REST_FRAMEWORK={**settings.REST_FRAMEWORK, "DEFAULT_THROTTLE_RATES": rates}
    )
