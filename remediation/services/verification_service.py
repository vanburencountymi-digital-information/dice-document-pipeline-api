import logging

from common.error_logging.client import ErrorLoggingClient
from remediation.adapters.base import VerificationAdapter
from remediation.adapters.verification.severity import SEVERITY_RANK, Severity
from remediation.adapters.verification.vera_pdf import VeraPDFAdapter
from remediation.models import (
    Remediation,
    RemediationArtifact,
    VerificationResult,
)
from remediation.services.artifact_service import ArtifactService
from remediation.verification_types import FailedRule, FailedRuleDict

LOGGER = logging.getLogger(__name__)


def _format_failure_summary(failed_rules: list[FailedRule]) -> str:
    """Short human-readable rendering of failed rules (simplify_vera_printouts), replacing
    the raw veraPDF XML report that used to be dumped straight into `Remediation.error` —
    worst severity first, so the most-blocking issue is always the first line under the
    header.
    """
    total_checks = sum(rule.failed_checks for rule in failed_rules)
    header = f"{len(failed_rules)} rules failed, {total_checks} checks"
    ordered = sorted(failed_rules, key=lambda rule: SEVERITY_RANK.index(rule.severity))
    lines = [
        f"  - {rule.severity.label.upper():<12} {rule.clause} "
        f"{rule.description} ({rule.failed_checks} checks)"
        for rule in ordered
    ]
    return "\n".join([header, *lines])


class AlreadyCompliant(Exception):
    """Not an error — raised by `PrecheckService` when the document already passes
    verification, signalling the pipeline to stop early and mark the job complete
    (ADR 0003)."""


class NotCompliant(Exception):
    """Not an error — raised by `PostCheckService` when the document still isn't compliant
    after remediation, signalling the job should be marked noncompliant (ADR 0003, ADR 0024).

    `str(exc)` is a short human-readable severity-ranked summary (simplify_vera_printouts) —
    never the raw veraPDF XML report.
    """

    def __init__(self, failed_rules: list[FailedRule]) -> None:
        self.failed_rules = failed_rules
        super().__init__(_format_failure_summary(failed_rules))


class PostCheckUnavailable(Exception):
    """Raised by `PostCheckService` when its own adapter crashed rather than producing a
    verdict (ADR 0013's `handle_verification_error`), signalling the job should be marked
    `ERROR` — no verdict means nothing for a reviewer to look at (ADR 0024). Deliberately
    not a `NotCompliant`, so a caller can't confuse the two.
    """


class VerificationService(ArtifactService):
    """Shared machinery for stages 1 and 6 of ADR 0003's pipeline — precheck and postcheck.

    Runs a `VerificationAdapter` against a PDF, records the outcome, and returns `pdf_uri`
    unchanged (verification doesn't transform the document) so every pipeline step shares the
    same "URI in, URI out" shape. Not instantiated directly — see `PrecheckService`/
    `PostCheckService`, which each fix `step` and `handle_result`.
    """

    def __init__(
        self,
        adapter: VerificationAdapter | None = None,
        error_logging: ErrorLoggingClient | None = None,
    ) -> None:
        super().__init__(error_logging)
        self.adapter = adapter or VeraPDFAdapter()

    def resume_completed(self, remediation: Remediation, output_uri: str) -> str:
        """Replays the stored verdict rather than just returning early (ADR 0019) —
        `handle_result` is what raises `AlreadyCompliant`/`NotCompliant`, so skipping it
        would let a redelivered task carry on past a decision the first run already made.
        """
        previous = VerificationResult.objects.get(remediation=remediation, step=self.step)
        self.handle_result(
            previous.is_compliant,
            [
                FailedRule(
                    clause=rule["clause"],
                    test_number=rule["test_number"],
                    description=rule["description"],
                    failed_checks=rule["failed_checks"],
                    severity=Severity(rule["severity"]),
                )
                for rule in previous.failed_rules
            ],
        )
        return output_uri

    def _run(self, remediation: Remediation, *, pdf_uri: str) -> str:
        try:
            with self.local_input_copy(pdf_uri) as pdf_path:
                outcome = self.adapter.validate(pdf_path)
        except Exception as exc:
            LOGGER.exception("remediation %s: %s adapter failed", remediation.id, self.step)
            self.report_exception(exc, remediation)
            self.mark_failed(remediation, str(exc))
            self.handle_verification_error(str(exc))
            return pdf_uri

        VerificationResult.objects.create(
            remediation=remediation,
            step=self.step,
            is_compliant=outcome.is_compliant,
            verapdf_version=outcome.verapdf_version,
            failed_rules=[
                FailedRuleDict(
                    clause=rule.clause,
                    test_number=rule.test_number,
                    description=rule.description,
                    failed_checks=rule.failed_checks,
                    severity=rule.severity.value,
                )
                for rule in outcome.failed_rules
            ],
        )
        self.mark_completed(remediation, output_uri=pdf_uri)
        self.handle_result(outcome.is_compliant, outcome.failed_rules)
        return pdf_uri

    def handle_result(self, is_compliant: bool, failed_rules: list[FailedRule]) -> None:
        raise NotImplementedError

    def handle_verification_error(self, error: str) -> None:
        """Called instead of `handle_result` when the adapter itself couldn't produce a
        verdict at all (ADR 0013) — e.g. veraPDF crashed, as opposed to running and finding
        the document non-compliant. Default: treat as inconclusive and let the pipeline
        continue — `PrecheckService` doesn't need this to mean "already compliant" any more
        than it needs a real non-compliant verdict to. `PostCheckService` overrides this,
        since there's no later check for it to fall back on.
        """


class PrecheckService(VerificationService):
    step = RemediationArtifact.Step.PRECHECK

    def handle_result(self, is_compliant: bool, failed_rules: list[FailedRule]) -> None:
        if is_compliant:
            raise AlreadyCompliant


class PostCheckService(VerificationService):
    step = RemediationArtifact.Step.POSTCHECK

    def handle_result(self, is_compliant: bool, failed_rules: list[FailedRule]) -> None:
        if not is_compliant:
            raise NotCompliant(failed_rules)

    def handle_verification_error(self, error: str) -> None:
        raise PostCheckUnavailable(f"postcheck could not run: {error}")
