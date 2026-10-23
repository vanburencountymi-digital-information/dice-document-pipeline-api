from django.conf import settings

from common.error_logging_client import ErrorLoggingClient


def config_warnings() -> list[str]:
    """Plain-English problems with the current settings that don't stop the app from starting
    but would make remediation quietly fail or lose files (DIC-2321). Empty when all is well.
    """
    warnings = []
    if not settings.S3_BUCKET_NAME:
        warnings.append(
            "S3_BUCKET_NAME is not set, so files are kept on local disk. That disk is lost when "
            "a container restarts and isn't shared between the web and worker containers."
        )
    if settings.RUN_ALT_TEXT and not settings.ANTHROPIC_API_KEY:
        warnings.append("RUN_ALT_TEXT is on but ANTHROPIC_API_KEY is not set.")
    if settings.RUN_OCR and not settings.OPENDATALOADER_HYBRID_URL:
        warnings.append("RUN_OCR is on but OPENDATALOADER_HYBRID_URL is not set.")
    if settings.REQUIRE_HTTPS and not settings.ORIGIN_LOCK_SECRET:
        warnings.append(
            "ORIGIN_LOCK_SECRET is not set, so requests that skip Cloudflare (e.g. via the "
            "run.app address) are not refused. Ignore this if the app isn't behind Cloudflare."
        )
    return warnings


def report_config_warnings() -> None:
    """Reports each config warning through `ErrorLoggingClient`, once per call. Called from
    `CommonConfig.ready()` (ADR 0035). Nothing is sent when monitoring has no DSN, as locally.
    """
    for warning in config_warnings():
        ErrorLoggingClient().report_warning(f"Config warning: {warning}")
