from typing import Any

from common.error_logging.constants import ErrorLoggingContextKey, ErrorLoggingTag
from common.error_logging_client import ErrorLoggingClient
from remediation.adapters.base import FailedRule
from remediation.models import Remediation
from remediation.monitoring_serializers import FailedRuleMonitoringSerializer

# First part of the Sentry fingerprint for noncompliant remediations (ADR 0044); the failing
# rule IDs follow it. Changing it splits existing Sentry issues into new ones.
NONCOMPLIANT_FINGERPRINT = "noncompliant-remediation"


class NoncompliantReportService:
    """Sends the logging client a warning for a remediation that finished but failed postcheck
    (ADR 0044), so failures can be sorted into categories and fixed in batches.
    """

    def __init__(
        self,
        error_logging: ErrorLoggingClient | None = None,
        failed_rule_serializer: type[FailedRuleMonitoringSerializer] | None = None,
    ) -> None:
        self.error_logging = error_logging or ErrorLoggingClient()
        self.failed_rule_serializer = failed_rule_serializer or FailedRuleMonitoringSerializer

    def report(self, remediation: Remediation, failed_rules: list[FailedRule]) -> None:
        """Takes: the finished `remediation` and the `failed_rules` postcheck reported.
        Returns: nothing.

        The fingerprint is the sorted failing rule IDs, so every document that fails the
        same set of rules is one logging client issue with a count, not one issue per document.
        """
        rule_ids = sorted(rule.rule_id for rule in failed_rules)
        self.error_logging.report_warning(
            f"Remediation not fully compliant: {', '.join(rule_ids)}",
            tags=self._tags(remediation),
            fingerprint=[NONCOMPLIANT_FINGERPRINT, *rule_ids],
            context=self._context(remediation, failed_rules),
        )

    def _tags(self, remediation: Remediation) -> dict[str, str]:
        """The searchable logging client tags. Takes: the `remediation`. Returns: tag names to
        string values.
        """
        return {
            ErrorLoggingTag.REMEDIATION_ID: str(remediation.id),
            ErrorLoggingTag.SERVICE_ACCOUNT_ID: str(remediation.service_account_id),
            ErrorLoggingTag.PIPELINE_VERSION: remediation.pipeline_version,
        }

    def _context(self, remediation: Remediation, failed_rules: list[FailedRule]) -> dict[str, Any]:
        """The detail block shown on the error logging event: what's needed to find and re-run the
        job. The tags aren't repeated here, because error client already shows them.
        `content_hash` is the source document's ID.

        Takes: the `remediation` and its `failed_rules`. Returns: the block's field names to
        values.
        """
        return {
            ErrorLoggingContextKey.CONTENT_HASH: remediation.content_hash,
            ErrorLoggingContextKey.FAILED_RULES: self.failed_rule_serializer(
                failed_rules, many=True
            ).data,
        }
