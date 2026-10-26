from django.conf import settings

from common.db_backup_adapter import DbBackupAdapter
from common.error_logging_client import ErrorLoggingClient

# The `job` tag on error reports, so Sentry alerts can pick out a failed backup.
JOB_DATABASE_DUMP = "database_dump"
# The name of the backup bucket's entry in `settings.STORAGES`, fixed by django-dbbackup.
BACKUP_STORAGE_ALIAS = "dbbackup"


class DatabaseDumpError(Exception):
    """Raised when a backup couldn't be made or uploaded. Already reported to monitoring."""


class DatabaseDumpService:
    """Backs up the database to the backup bucket, so a copy exists outside the database host
    (DIC-2540), and reports any failure to monitoring.
    """

    def __init__(
        self,
        adapter: DbBackupAdapter | None = None,
        error_logging: ErrorLoggingClient | None = None,
    ) -> None:
        self.adapter = adapter or DbBackupAdapter()
        self.error_logging = error_logging or ErrorLoggingClient()

    def run(self) -> None:
        """Takes: nothing. Returns: nothing. Raises `DatabaseDumpError` on any failure
        (including no backup bucket being set up), after reporting it to monitoring.
        """
        try:
            self._require_backup_bucket()
            self.adapter.backup()
        except Exception as exc:
            self.error_logging.report_exception(exc, tags={"job": JOB_DATABASE_DUMP})
            raise DatabaseDumpError(f"Database dump failed: {exc}") from exc

    def _require_backup_bucket(self) -> None:
        # Without this, django-dbbackup would quietly write backups to the container's disk.
        if BACKUP_STORAGE_ALIAS not in settings.STORAGES:
            raise DatabaseDumpError(
                "No backup bucket is set up: DB_DUMP_BUCKET_NAME is empty, so there is "
                "nowhere to upload the backup."
            )
