from unittest.mock import create_autospec

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from common.db_backup_adapter import DbBackupAdapter
from common.error_logging_client import ErrorLoggingClient
from common.services import (
    JOB_DATABASE_BACKUP,
    DatabaseBackupError,
    DatabaseBackupService,
)


class DatabaseBackupServiceTests(SimpleTestCase):
    def setUp(self) -> None:
        self.adapter = create_autospec(DbBackupAdapter, instance=True, spec_set=True)
        self.error_logging = create_autospec(ErrorLoggingClient, instance=True, spec_set=True)
        self.service = DatabaseBackupService(self.adapter, self.error_logging)

    def test_runs_the_backup(self) -> None:
        self.service.run()

        self.adapter.backup.assert_called_once_with()
        self.error_logging.report_exception.assert_not_called()

    def test_backup_failure_is_reported_with_a_tag_and_raised(self) -> None:
        error = OSError("network")
        self.adapter.backup.side_effect = error

        with self.assertRaises(DatabaseBackupError):
            self.service.run()

        self.error_logging.report_exception.assert_called_once_with(
            error, tags={"job": JOB_DATABASE_BACKUP}
        )

    def test_a_setup_problem_is_reported_and_raised_like_any_failure(self) -> None:
        error = ImproperlyConfigured("No backup bucket is set up: DB_BACKUP_BUCKET_NAME is empty")
        self.adapter.backup.side_effect = error

        with self.assertRaisesRegex(DatabaseBackupError, "DB_BACKUP_BUCKET_NAME"):
            self.service.run()

        self.error_logging.report_exception.assert_called_once_with(
            error, tags={"job": JOB_DATABASE_BACKUP}
        )
