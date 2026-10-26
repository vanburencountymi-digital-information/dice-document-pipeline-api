from io import StringIO
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from common.services import DatabaseBackupError

COMMAND_MODULE = "common.management.commands.backup_database"


@patch(f"{COMMAND_MODULE}.DatabaseBackupService", autospec=True, spec_set=True)
class BackupDatabaseCommandTests(SimpleTestCase):
    def test_runs_the_backup(self, mock_service_cls: MagicMock) -> None:
        out = StringIO()

        call_command("backup_database", stdout=out)

        mock_service_cls.return_value.run.assert_called_once_with()
        self.assertIn("Backup uploaded", out.getvalue())

    def test_failure_exits_non_zero(self, mock_service_cls: MagicMock) -> None:
        mock_service_cls.return_value.run.side_effect = DatabaseBackupError("nope")

        with self.assertRaises(CommandError):
            call_command("backup_database")
