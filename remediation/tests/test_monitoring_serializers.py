from django.test import SimpleTestCase
from parameterized import parameterized

from remediation.adapters.verification.severity import Severity
from remediation.serializers.monitoring_serializers import FailedRuleMonitoringSerializer
from remediation.verification_types import FailedRule


class FailedRuleMonitoringSerializerTests(SimpleTestCase):
    def test_shows_only_the_rule_id_check_count_and_severity(self) -> None:
        rule = FailedRule("7.1", "11", "StructTreeRoot missing", 3, Severity.CRITICAL)

        data = FailedRuleMonitoringSerializer(rule).data

        self.assertEqual(
            dict(data),
            {"rule": "7.1:11", "failed_checks": 3, "severity": Severity.CRITICAL.value},
        )

    @parameterized.expand([(severity.name, severity) for severity in Severity])
    def test_severity_is_its_plain_string_value(self, _name, severity) -> None:
        rule = FailedRule("7.21", "7", "font missing CIDSet", 1, severity)

        data = FailedRuleMonitoringSerializer(rule).data

        self.assertEqual(data["severity"], severity.value)
        self.assertIs(type(data["severity"]), str)

    def test_many_keeps_the_order_given(self) -> None:
        rules = [
            FailedRule("7.21", "7", "font missing CIDSet", 1, Severity.MINOR),
            FailedRule("7.1", "11", "StructTreeRoot missing", 2, Severity.CRITICAL),
        ]

        data = FailedRuleMonitoringSerializer(rules, many=True).data

        self.assertEqual([item["rule"] for item in data], ["7.21:7", "7.1:11"])
