from enum import StrEnum
from typing import Final

# Key under which `report_warning`'s `context` dict is attached to the event.
DETAILS_CONTEXT_KEY = "details"

# `Final` keeps it the literal type the Sentry SDK wants for `level` (a StrEnum fails mypy).
WARNING_LEVEL: Final = "warning"


class ErrorLoggingTag(StrEnum):
    """Every searchable tag name we attach to Sentry events. Renaming one breaks existing
    Sentry searches and alerts."""

    JOB = "job"
    REMEDIATION_ID = "remediation_id"
    SERVICE_ACCOUNT_ID = "service_account_id"
    PIPELINE_VERSION = "pipeline_version"
    STEP = "step"


class ErrorLoggingContextKey(StrEnum):
    """Names of the extra detail fields shown on a Sentry event, beyond the tags."""

    CONTENT_HASH = "content_hash"
    FAILED_RULES = "failed_rules"
