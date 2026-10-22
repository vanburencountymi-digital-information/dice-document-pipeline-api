from enum import StrEnum


class ThrottleScope(StrEnum):
    """Names a throttle's rate in `REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]` and its counters."""

    FAILED_AUTH = "failed_auth"
    CLIENT_IP = "client_ip"


class LimitKind(StrEnum):
    """Which limit a 429 refers to. `in_flight` joins when that cap is enforced."""

    RATE = "rate"
