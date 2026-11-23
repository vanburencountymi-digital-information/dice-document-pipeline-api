from rest_framework import serializers


class FailedRuleMonitoringSerializer(serializers.Serializer):
    """The fields of a `FailedRule` shown on a Sentry event (ADR 0044) — only what's needed
    to categorize and re-run, not the rule's description.

    Its own module, not `serializers.py`, because that file imports `services.py` and
    `services.py` uses this, which would be a circular import.
    """

    rule = serializers.CharField(source="rule_id")
    failed_checks = serializers.IntegerField()
    severity = serializers.CharField()
