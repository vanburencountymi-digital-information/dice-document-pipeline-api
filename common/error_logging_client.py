import sentry_sdk
from sentry_sdk.integrations.django import DjangoIntegration


class ErrorLoggingClient:
    """Wraps whichever error-monitoring SDK is in use (Sentry, ADR 0017) — the only place
    in the project that imports `sentry_sdk` directly, so swapping monitoring vendors, or
    adding a second one, never touches a calling app.
    """

    def configure(self, *, dsn: str, environment: str, release: str) -> None:
        """Starts error monitoring. An empty `dsn` (the local/test default) makes it a no-op."""
        sentry_sdk.init(
            dsn=dsn,
            integrations=[DjangoIntegration()],
            environment=environment,
            release=release,
            send_default_pii=False,
        )

    def report_exception(self, exc: Exception, *, tags: dict[str, str]) -> None:
        sentry_sdk.capture_exception(exc, tags=tags)

    def report_warning(self, message: str) -> None:
        """Reports something worth noticing that isn't an exception, e.g. a config problem
        found at startup (ADR 0035).
        """
        sentry_sdk.capture_message(message, level="warning")
