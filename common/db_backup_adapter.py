from django.core.management import call_command

# Part of every backup's file name, which is also how old backups are found for cleanup.
SERVER_NAME = "dice"


class DbBackupAdapter:
    """Wraps the `django-dbbackup` package's `dbbackup` command (DIC-2540)."""

    def backup(self) -> None:
        """Takes: nothing. Returns: nothing. Raises whatever `dbbackup` raises on failure.

        Dumps the default database with `pg_dump`, saves it to the `dbbackup` storage, then
        deletes all but the newest `DBBACKUP_CLEANUP_KEEP` backups. Does not ask questions.
        """
        call_command("dbbackup", servername=SERVER_NAME, clean=True, interactive=False)
