import logging
import time
from datetime import timedelta

from django.conf import settings
from django.tasks import task
from django.utils import timezone

from remediation.models import Remediation, RemediationCallback
from remediation.services import (
    AlreadyCompliant,
    AltTextService,
    FontRepairService,
    LinkService,
    MetadataService,
    NotCompliant,
    OCRService,
    PostCheckService,
    PrecheckService,
    RemediationService,
    ScoringService,
    build_download_url,
)
from remediation.webhook_client import WebhookClient, WebhookDeliveryError

# ScoringService is non-blocking (add_confidence_scoring) — placed last before
# PostCheckService so it scores the same file veraPDF is about to validate. If
# PrecheckService raises AlreadyCompliant, the loop exits before this ever runs.
PIPELINE_STEPS = [
    PrecheckService,
    OCRService,
    FontRepairService,
    MetadataService,
    LinkService,
    AltTextService,
    ScoringService,
    PostCheckService,
]

LOGGER = logging.getLogger(__name__)


@task()
def send_webhook_notification(callback_id: str, attempt: int = 1) -> None:
    """Delivers one webhook subscriber's notification for its `Remediation` (ADR 0015).

    Keyed by `RemediationCallback.id`, not `remediation_id` — each subscriber on the same
    attempt retries independently of any others registered on it.

    `ImmediateBackend` (the test backend) doesn't support `run_after` at all —
    `Task.using(run_after=...)` validates eagerly and raises `InvalidTask` the moment it's
    constructed, not when enqueued — so this checks `supports_defer` before attaching one.
    Under `ImmediateBackend` retries happen back-to-back with no real delay; the default
    `DatabaseBackend` (ADR 0020) holds each retry until its backoff has passed.
    """
    callback = RemediationCallback.objects.select_related("remediation__service_account").get(
        pk=callback_id
    )
    remediation = callback.remediation

    payload: dict[str, str] = {
        "remediation_id": str(remediation.id),
        "document_id": remediation.content_hash,
        "status": remediation.status,
    }
    if remediation.final_output_uri:
        payload["download_url"] = build_download_url(remediation)
    if remediation.status == Remediation.JobStatus.FAILED:
        payload["error"] = remediation.error

    try:
        WebhookClient().notify(
            callback.callback_url,
            secret=remediation.service_account.webhook_secret,
            payload=payload,
        )
    except WebhookDeliveryError:
        LOGGER.exception("callback %s: webhook delivery failed (attempt %s)", callback_id, attempt)
        if attempt >= settings.MAX_WEBHOOK_ATTEMPTS:
            raise
        retry_task = send_webhook_notification
        if send_webhook_notification.get_backend().supports_defer:
            delay = settings.WEBHOOK_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
            retry_task = retry_task.using(run_after=timezone.now() + timedelta(seconds=delay))
        retry_task.enqueue(callback_id, attempt=attempt + 1)


@task()
def process_remediation(remediation_id: str) -> None:
    """Runs a `Remediation` job to completion, per ADR 0003's pipeline shape (amended by
    ADR 0013).

    Each step in `PIPELINE_STEPS` catches its own exceptions, records them on its own `RemediationArtifact`,
    and returns the input `pdf_uri` unchanged, so the pipeline always reaches `PostCheckService`.

    Jobs are ended early if precheck marks `AlreadyCompliant`.

    Every terminal branch records `final_output_uri` (ADR 0015) — success or failure alike,
    since a partially remediated document is still worth serving (ADR 0013). `finally`
    (not a line after the try/except/else, since the generic `except Exception` branch
    re-raises) enqueues `send_webhook_notification` for every `RemediationCallback`
    registered on this attempt, once the row's final status is already committed.
    """
    service = RemediationService()
    remediation = service.get(remediation_id)
    # A redelivered task for an already-finished attempt (ADR 0019) — returning before the
    # try/finally below keeps timestamps intact and doesn't re-send every webhook. Retries
    # are unaffected: they always create a new `Remediation` row (ADR 0014).
    if remediation.status in (Remediation.JobStatus.COMPLETE, Remediation.JobStatus.FAILED):
        LOGGER.info(
            "remediation %s: already %s, skipping redelivered task",
            remediation_id,
            remediation.status,
        )
        return
    service.mark_running(remediation)
    pdf_uri = remediation.source_pdf_uri
    LOGGER.info(
        "remediation %s: starting pipeline (%s)", remediation_id, remediation.original_filename
    )
    job_start = time.monotonic()
    try:
        for step_service_cls in PIPELINE_STEPS:
            # call the service name as a class
            step_service = step_service_cls()

            if step_service.is_disabled():
                step_service.record_skip(remediation)
                LOGGER.info(
                    "remediation %s: skipping %s (disabled)", remediation_id, step_service.step
                )
                continue

            LOGGER.info("remediation %s: starting %s", remediation_id, step_service.step)
            step_start = time.monotonic()
            pdf_uri = step_service.run(remediation, pdf_uri=pdf_uri)
            LOGGER.info(
                "remediation %s: finished %s in %.1fs",
                remediation_id,
                step_service.step,
                time.monotonic() - step_start,
            )

    except AlreadyCompliant:
        LOGGER.info("remediation %s: already compliant, marking complete", remediation_id)
        service.mark_complete(remediation, final_output_uri=remediation.source_pdf_uri)

    except NotCompliant as exc:
        LOGGER.info("remediation %s: postcheck did not pass", remediation_id)
        service.mark_failed(remediation, f"postcheck: {exc}", final_output_uri=pdf_uri)

    except Exception as exc:
        LOGGER.exception("remediation %s: pipeline step raised", remediation_id)
        service.mark_failed(remediation, str(exc), final_output_uri=pdf_uri)
        raise

    else:
        LOGGER.info(
            "remediation %s: pipeline complete in %.1fs",
            remediation_id,
            time.monotonic() - job_start,
        )
        service.mark_complete(remediation, final_output_uri=pdf_uri)

    finally:
        for callback_id in remediation.callbacks.values_list("id", flat=True):
            send_webhook_notification.enqueue(str(callback_id))
