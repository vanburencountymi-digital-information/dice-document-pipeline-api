from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from common.db_backup_adapter import SERVER_NAME, DbBackupAdapter


class DbBackupAdapterTests(SimpleTestCase):
    @patch("common.db_backup_adapter.call_command", autospec=True)
    def test_runs_dbbackup_with_cleanup_and_no_prompts(self, mock_call: MagicMock) -> None:
        DbBackupAdapter().backup()

        mock_call.assert_called_once_with(
            "dbbackup", servername=SERVER_NAME, clean=True, interactive=False
        )
