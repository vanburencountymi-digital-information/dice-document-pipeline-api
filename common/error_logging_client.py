import sentry_sdk


class ErrorLoggingClient:
    """Wraps whichever error-monitoring SDK is in use (Sentry, ADR 0017) — the only place
    in the project that imports `sentry_sdk` directly, so swapping monitoring vendors, or
    adding a second one, never touches a calling app.
    """

    def report_exception(self, exc: Exception, *, tags: dict[str, str]) -> None:
        sentry_sdk.capture_exception(exc, tags=tags)
