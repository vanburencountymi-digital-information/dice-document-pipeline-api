from django.tasks import task

from remediation.services import (
    AlreadyCompliant,
    NotCompliant,
    OCRService,
    PostCheckService,
    PrecheckService,
    RemediationService,
)

PIPELINE_STEPS = [
    PrecheckService,
    OCRService,
    # TaggingService.finalize_tags, AltTextService, LinkService go here once built.
    PostCheckService,
]


@task()
def process_remediation(remediation_id: str) -> None:
    """Runs a `Remediation` job to completion, per ADR 0003's pipeline shape.

    Each step in `PIPELINE_STEPS` takes the current PDF's URI and hands back the URI to pass to the next step (verification steps pass it through unchanged). Precheck and postcheck are the two steps that can end the job early — they signal that by raising `AlreadyCompliant`/`NotCompliant` instead of returning normally, so this loop doesn't need special-case branching around every step to know when to stop.
    """
    service = RemediationService()
    remediation = service.get(remediation_id)
    service.mark_running(remediation)
    pdf_uri = remediation.source_pdf_uri
    try:
        for step_service_cls in PIPELINE_STEPS:
            # call the service name as a class
            step_service = step_service_cls()

            if step_service.is_disabled():
                step_service.record_skip(remediation)
                continue

            pdf_uri = step_service.run(remediation, pdf_uri=pdf_uri)

    except AlreadyCompliant:
        service.mark_complete(remediation)

    except NotCompliant:
        service.mark_failed(remediation, "postcheck: not PDF/UA-1 compliant")

    except Exception as exc:
        service.mark_failed(remediation, str(exc))
        raise

    else:
        service.mark_complete(remediation)
