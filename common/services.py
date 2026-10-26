from common.db_backup_adapter import DbBackupAdapter
from common.error_logging_client import ErrorLoggingClient

# The `job` tag on error reports, so Sentry alerts can pick out a failed backup.
JOB_DATABASE_BACKUP = "database_backup"


class DatabaseBackupError(Exception):
    """Raised when a backup couldn't be made, uploaded or set up. Already reported to
    monitoring.
    """


class DatabaseBackupService:
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
        """Takes: nothing. Returns: nothing. Raises `DatabaseBackupError` on any failure
        (including no backup bucket being set up), after reporting it to monitoring.
        """
        try:
            self.adapter.backup()
        except Exception as exc:
            self.error_logging.report_exception(exc, tags={"job": JOB_DATABASE_BACKUP})
            raise DatabaseBackupError(f"Database backup failed: {exc}") from exc
