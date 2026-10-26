from unittest.mock import create_autospec

from django.test import SimpleTestCase, override_settings

from common.db_backup_adapter import DbBackupAdapter
from common.error_logging_client import ErrorLoggingClient
from common.services import (
    BACKUP_STORAGE_ALIAS,
    JOB_DATABASE_DUMP,
    DatabaseDumpError,
    DatabaseDumpService,
)

WITH_BUCKET = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    BACKUP_STORAGE_ALIAS: {"BACKEND": "storages.backends.s3.S3Storage"},
}


@override_settings(STORAGES=WITH_BUCKET)
class DatabaseDumpServiceTests(SimpleTestCase):
    def setUp(self) -> None:
        self.adapter = create_autospec(DbBackupAdapter, instance=True, spec_set=True)
        self.error_logging = create_autospec(ErrorLoggingClient, instance=True, spec_set=True)
        self.service = DatabaseDumpService(self.adapter, self.error_logging)

    def test_runs_the_backup(self) -> None:
        self.service.run()

        self.adapter.backup.assert_called_once_with()
        self.error_logging.report_exception.assert_not_called()

    def test_backup_failure_is_reported_with_a_tag_and_raised(self) -> None:
        error = OSError("network")
        self.adapter.backup.side_effect = error

        with self.assertRaises(DatabaseDumpError):
            self.service.run()

        self.error_logging.report_exception.assert_called_once_with(
            error, tags={"job": JOB_DATABASE_DUMP}
        )

    @override_settings(STORAGES={"default": WITH_BUCKET["default"]})
    def test_no_backup_bucket_is_reported_and_raised_without_backing_up(self) -> None:
        with self.assertRaisesRegex(DatabaseDumpError, "DB_DUMP_BUCKET_NAME"):
            self.service.run()

        self.adapter.backup.assert_not_called()
        self.error_logging.report_exception.assert_called_once()
