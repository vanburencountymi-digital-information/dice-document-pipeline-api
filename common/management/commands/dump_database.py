"""Backs up the database to the backup bucket (DIC-2540).

Cloud Scheduler starts this as a Cloud Run job every night. It exits non-zero on failure, so
the run shows as failed; the failure is also reported to Sentry first.
"""

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from common.services import DatabaseDumpError, DatabaseDumpService


class Command(BaseCommand):
    help = "Backs up the database to the backup bucket (DIC-2540)."

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            DatabaseDumpService().run()
        except DatabaseDumpError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write("Backup uploaded.")
