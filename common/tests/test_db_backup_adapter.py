from unittest.mock import MagicMock, patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from common.db_backup_adapter import (
    BACKUP_STORAGE_ALIAS,
    DBBACKUP_COMMAND,
    DBBACKUP_SERVER_NAME,
    DbBackupAdapter,
)

WITH_BUCKET = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    BACKUP_STORAGE_ALIAS: {"BACKEND": "storages.backends.s3.S3Storage"},
}


class DbBackupAdapterTests(SimpleTestCase):
    @override_settings(STORAGES=WITH_BUCKET)
    @patch("common.db_backup_adapter.call_command", autospec=True)
    def test_runs_dbbackup_with_cleanup_and_no_prompts(self, mock_call: MagicMock) -> None:
        DbBackupAdapter().backup()

        mock_call.assert_called_once_with(
            DBBACKUP_COMMAND, servername=DBBACKUP_SERVER_NAME, clean=True, interactive=False
        )

    @override_settings(STORAGES={"default": WITH_BUCKET["default"]})
    @patch("common.db_backup_adapter.call_command", autospec=True)
    def test_no_backup_bucket_raises_without_running_dbbackup(self, mock_call: MagicMock) -> None:
        with self.assertRaisesRegex(ImproperlyConfigured, "DB_BACKUP_BUCKET_NAME"):
            DbBackupAdapter().backup()

        mock_call.assert_not_called()
