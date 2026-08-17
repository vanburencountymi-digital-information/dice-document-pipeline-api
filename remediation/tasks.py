import logging
import time

from django.tasks import task

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
)

PIPELINE_STEPS = [
    PrecheckService,
    OCRService,
    FontRepairService,
    MetadataService,
    LinkService,
    AltTextService,
    PostCheckService,
]

LOGGER = logging.getLogger(__name__)


@task()
def process_remediation(remediation_id: str) -> None:
    """Runs a `Remediation` job to completion, per ADR 0003's pipeline shape.

    Each step in `PIPELINE_STEPS` takes the current PDF's URI and hands back the URI to pass to the next step (verification steps pass it through unchanged). Precheck and postcheck are the two steps that can end the job early — they signal that by raising `AlreadyCompliant`/`NotCompliant` instead of returning normally, so this loop doesn't need special-case branching around every step to know when to stop.
    """
    service = RemediationService()
    remediation = service.get(remediation_id)
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
        service.mark_complete(remediation)

    except NotCompliant as exc:
        LOGGER.info("remediation %s: postcheck failed, not PDF/UA-1 compliant", remediation_id)
        service.mark_failed(remediation, f"postcheck: not PDF/UA-1 compliant: {exc}")

    except Exception as exc:
        LOGGER.exception("remediation %s: pipeline step raised", remediation_id)
        service.mark_failed(remediation, str(exc))
        raise

    else:
        LOGGER.info(
            "remediation %s: pipeline complete in %.1fs",
            remediation_id,
            time.monotonic() - job_start,
        )
        service.mark_complete(remediation)
