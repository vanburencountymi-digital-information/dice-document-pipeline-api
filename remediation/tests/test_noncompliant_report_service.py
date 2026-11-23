from unittest.mock import create_autospec

from django.test import TestCase

from common.error_logging.client import ErrorLoggingClient
from remediation.adapters.verification.severity import Severity
from remediation.serializers.monitoring_serializers import FailedRuleMonitoringSerializer
from remediation.services.noncompliant_report_service import (
    NONCOMPLIANT_FINGERPRINT,
    NoncompliantReportService,
)
from remediation.tests.factories import RemediationFactory
from remediation.verification_types import FailedRule


class NoncompliantReportServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.remediation = RemediationFactory(pipeline_version="5.11.0")

    def test_sends_warning_fingerprinted_on_sorted_rule_ids(self) -> None:
        error_logging = create_autospec(ErrorLoggingClient, instance=True, spec_set=True)
        rules = [
            FailedRule("7.21", "7", "font missing CIDSet", 3, Severity.MINOR),
            FailedRule("7.1", "11", "StructTreeRoot missing", 1, Severity.CRITICAL),
        ]

        NoncompliantReportService(error_logging=error_logging).report(self.remediation, rules)

        _, kwargs = error_logging.report_warning.call_args
        self.assertEqual(kwargs["fingerprint"], [NONCOMPLIANT_FINGERPRINT, "7.1:11", "7.21:7"])
        self.assertEqual(kwargs["tags"]["remediation_id"], str(self.remediation.id))
        self.assertEqual(kwargs["tags"]["pipeline_version"], "5.11.0")
        self.assertEqual(kwargs["context"]["content_hash"], self.remediation.content_hash)
        self.assertEqual(
            kwargs["context"]["failed_rules"],
            [
                {"rule": "7.21:7", "failed_checks": 3, "severity": Severity.MINOR.value},
                {"rule": "7.1:11", "failed_checks": 1, "severity": Severity.CRITICAL.value},
            ],
        )

    def test_failed_rules_come_from_the_injected_serializer(self) -> None:
        error_logging = create_autospec(ErrorLoggingClient, instance=True, spec_set=True)
        serializer_cls = create_autospec(FailedRuleMonitoringSerializer, spec_set=True)
        serializer_cls.return_value.data = [{"rule": "stub"}]
        rules = [FailedRule("7.1", "11", "x", 1, Severity.CRITICAL)]

        NoncompliantReportService(
            error_logging=error_logging, failed_rule_serializer=serializer_cls
        ).report(self.remediation, rules)

        serializer_cls.assert_called_once_with(rules, many=True)
        _, kwargs = error_logging.report_warning.call_args
        self.assertEqual(kwargs["context"]["failed_rules"], [{"rule": "stub"}])

    def test_same_rules_in_any_order_share_a_fingerprint(self) -> None:
        error_logging = create_autospec(ErrorLoggingClient, instance=True, spec_set=True)
        a = FailedRule("7.1", "11", "x", 1, Severity.CRITICAL)
        b = FailedRule("7.21", "7", "y", 1, Severity.MINOR)

        NoncompliantReportService(error_logging=error_logging).report(self.remediation, [a, b])
        NoncompliantReportService(error_logging=error_logging).report(self.remediation, [b, a])

        calls = error_logging.report_warning.call_args_list
        self.assertEqual(calls[0].kwargs["fingerprint"], calls[1].kwargs["fingerprint"])
