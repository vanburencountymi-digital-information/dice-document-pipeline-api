from dataclasses import dataclass
from typing import TypedDict

from remediation.adapters.verification.severity import Severity


@dataclass(frozen=True)
class FailedRule:
    """One failed veraPDF PDF/UA-1 rule, from `VerificationAdapter.validate()`'s structured
    report — replaces handing the raw XML report string around (simplify_vera_printouts).

    `test_number` is kept alongside `clause` (not just for provenance) because `clause` alone
    isn't always specific enough to classify severity from — clauses 7.1 and 7.2 each bundle
    rules spanning every severity tier under one clause number (see
    `verification/severity.py`'s `RULE_SEVERITY`), so `test_number` is what `classify()`
    actually keys its exact overrides on for those two.
    """

    clause: str
    test_number: str
    description: str
    failed_checks: int
    severity: Severity

    @property
    def rule_id(self) -> str:
        """The rule's short ID, e.g. `7.1:11` (`clause:test_number`). Used wherever rules are
        grouped or compared, such as Sentry's grouping of noncompliant remediations (ADR 0044).

        Note: `pdf_quality_report` still writes the same ID as `7.1/11` (its saved baselines
        may depend on that), and `severity.py` keys rules as `7.1-11`.
        """
        return f"{self.clause}:{self.test_number}"


class FailedRuleDict(TypedDict):
    """The JSON shape one `FailedRule` becomes in `VerificationResult.failed_rules`
    (a `JSONField`, so `severity` here is `Severity.value` — a plain string, not the enum).
    """

    clause: str
    test_number: str
    description: str
    failed_checks: int
    severity: str
