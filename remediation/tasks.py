from django.tasks import task

from remediation.services import RemediationService


@task()
def process_remediation(remediation_id: str) -> None:
    """Runs a `Remediation` job to completion.

    Placeholder pipeline for now — flips `QUEUED` -> `RUNNING` -> `COMPLETE`
    with no real work in between. The actual pipeline (veraPDF check, OCR,
    auto-tagging, alt-text) is still under research (see
    implementation_plan.md) and isn't wired in here yet. This exists to
    prove the django.tasks enqueue/run path end to end.
    """
    service = RemediationService()
    remediation = service.get(remediation_id)
    service.mark_running(remediation)
    try:
        pass  # real pipeline goes here once engine choices are made
    except Exception as exc:
        service.mark_failed(remediation, str(exc))
        raise
    else:
        service.mark_complete(remediation)
