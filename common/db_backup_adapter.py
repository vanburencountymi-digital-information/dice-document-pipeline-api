# Imported by config/settings.py while it loads, so nothing here may read `settings` at import
# time (only inside methods), or settings would import itself.
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command

# The names django-dbbackup fixes. Defined once here, so settings, the service and error
# monitoring can't drift apart from the package or from each other.
DBBACKUP_COMMAND = "dbbackup"
# Its entry in `settings.STORAGES`. Without one it quietly saves backups to local disk.
BACKUP_STORAGE_ALIAS = "dbbackup"
# Where it logs a failed backup's traceback.
DBBACKUP_LOGGER = "dbbackup"
# Part of every backup's file name, which is also how old backups are found for cleanup.
DBBACKUP_SERVER_NAME = "dice"


class DbBackupAdapter:
    """Wraps the `django-dbbackup` package's `dbbackup` command (DIC-2540)."""

    def backup(self) -> None:
        """Takes: nothing. Returns: nothing. Raises `ImproperlyConfigured` if no backup bucket
        is set up, and whatever `dbbackup` raises if the backup fails.

        Dumps the default database with `pg_dump`, saves it to the `dbbackup` storage, then
        deletes all but the newest `DBBACKUP_CLEANUP_KEEP` backups. Does not ask questions.
        """
        # settings.py only adds the storage entry when DB_BACKUP_BUCKET_NAME is set. The
        # package would otherwise save the backup to this container's local disk and lose it.
        if BACKUP_STORAGE_ALIAS not in settings.STORAGES:
            raise ImproperlyConfigured(
                "No backup bucket is set up: DB_BACKUP_BUCKET_NAME is empty, so there is "
                "nowhere to upload the backup."
            )
        call_command(
            DBBACKUP_COMMAND, servername=DBBACKUP_SERVER_NAME, clean=True, interactive=False
        )
