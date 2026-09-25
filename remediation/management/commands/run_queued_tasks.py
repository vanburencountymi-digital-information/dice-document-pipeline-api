"""The scheduled worker for deployed environments (ADR 0023).

Cloud Scheduler starts this as a Cloud Run job every 15 minutes, with the OCR server running
beside it as a sidecar container. Each run:

1. Exits straight away if nothing is queued — most runs, and why this costs almost nothing
   compared to an always-on worker.
2. Otherwise waits for the OCR sidecar to finish loading (only if OCR is switched on).
3. Runs django-tasks-db's worker in batch mode: everything that's due, then exit.

Don't give the job a container startup dependency on the OCR sidecar in Cloud Run: that
would make every run — including the empty ones — wait for OCR to load. This command waits
for OCR itself, and only when there's work.

Local dev doesn't use this: docker-compose's `worker` runs `db_worker` continuously.
"""

from typing import Any

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from remediation.hybrid_client import HybridServerNotReady, OpenDataLoaderHybridClient
from remediation.services import TaskQueueService


class Command(BaseCommand):
    help = "Runs every queued task that's due, then exits (the scheduled worker, ADR 0023)."

    def handle(self, *args: Any, **options: Any) -> None:
        if not TaskQueueService().has_ready_tasks():
            self.stdout.write("Nothing queued.")
            return

        if settings.RUN_OCR and settings.OPENDATALOADER_HYBRID_URL:
            self.stdout.write("Work queued; waiting for the OCR server to be ready...")
            try:
                OpenDataLoaderHybridClient(settings.OPENDATALOADER_HYBRID_URL).wait_until_ready(
                    timeout=settings.OCR_READY_TIMEOUT_SECONDS,
                    poll_interval=settings.OCR_READY_POLL_SECONDS,
                )
            except HybridServerNotReady as exc:
                # Non-zero exit, so the run shows as failed; the tasks stay queued for the
                # next scheduled run.
                raise CommandError(str(exc)) from exc

        call_command("db_worker", batch=True, startup_delay=False, reload=False)
