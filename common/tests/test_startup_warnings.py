from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings
from parameterized import parameterized

from common.startup_warnings import config_warnings, report_config_warnings

HEALTHY = {
    "S3_BUCKET_NAME": "bucket",
    "RUN_ALT_TEXT": True,
    "ANTHROPIC_API_KEY": "key",
    "RUN_OCR": True,
    "OPENDATALOADER_HYBRID_URL": "http://ocr:5002",
    "REQUIRE_HTTPS": True,
    "ORIGIN_LOCK_SECRET": "s3cret",
}


class ConfigWarningsTests(SimpleTestCase):
    @override_settings(**HEALTHY)
    def test_no_warnings_when_everything_is_set(self) -> None:
        self.assertEqual(config_warnings(), [])

    @parameterized.expand(
        [
            ("no_bucket", {"S3_BUCKET_NAME": ""}, "S3_BUCKET_NAME"),
            ("no_api_key", {"ANTHROPIC_API_KEY": ""}, "ANTHROPIC_API_KEY"),
            ("no_ocr_url", {"OPENDATALOADER_HYBRID_URL": ""}, "OPENDATALOADER_HYBRID_URL"),
            ("no_origin_secret", {"ORIGIN_LOCK_SECRET": ""}, "ORIGIN_LOCK_SECRET"),
        ]
    )
    def test_warns_about_each_missing_setting(self, _name, overrides, expected) -> None:
        with override_settings(**{**HEALTHY, **overrides}):
            warnings = config_warnings()

        self.assertEqual(len(warnings), 1)
        self.assertIn(expected, warnings[0])

    @parameterized.expand(
        [
            ("alt_text_off", {"RUN_ALT_TEXT": False, "ANTHROPIC_API_KEY": ""}),
            ("ocr_off", {"RUN_OCR": False, "OPENDATALOADER_HYBRID_URL": ""}),
            ("local_dev", {"REQUIRE_HTTPS": False, "ORIGIN_LOCK_SECRET": ""}),
        ]
    )
    def test_no_warning_for_a_missing_setting_whose_step_is_off(self, _name, overrides) -> None:
        with override_settings(**{**HEALTHY, **overrides}):
            self.assertEqual(config_warnings(), [])


class ReportConfigWarningsTests(SimpleTestCase):
    @override_settings(**{**HEALTHY, "S3_BUCKET_NAME": ""})
    @patch("common.startup_warnings.ErrorLoggingClient", autospec=True, spec_set=True)
    def test_reports_each_warning(self, mock_client_cls: MagicMock) -> None:
        report_config_warnings()

        mock_report = mock_client_cls.return_value.report_warning
        mock_report.assert_called_once()
        self.assertIn("S3_BUCKET_NAME", mock_report.call_args.args[0])

    @override_settings(**HEALTHY)
    @patch("common.startup_warnings.ErrorLoggingClient", autospec=True, spec_set=True)
    def test_sends_nothing_when_config_is_fine(self, mock_client_cls: MagicMock) -> None:
        report_config_warnings()

        mock_client_cls.return_value.report_warning.assert_not_called()
