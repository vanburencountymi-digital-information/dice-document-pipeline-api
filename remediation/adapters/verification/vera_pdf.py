import subprocess
from xml.etree import ElementTree

from remediation.adapters.base import (
    FailedRule,
    VerificationAdapter,
    VerificationOutcome,
)
from remediation.adapters.verification.severity import classify


class VeraPDFAdapter(VerificationAdapter):
    """Wraps the veraPDF CLI"""

    def __init__(self, flavour: str = "ua1"):
        self.flavour = flavour

    @property
    def name(self) -> str:
        return "Vera PDF Adapter"

    def run_vera_pdf(self, pdf_path: str) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                ["verapdf", "--flavour", self.flavour, "--format", "xml", pdf_path],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            self.raise_adapter_error(
                "verapdf executable not found — it's only installed in the Docker image "
            )
        if not result.stdout.strip():
            self.raise_adapter_error(
                f"verapdf produced no output (exit {result.returncode}): {result.stderr.strip()}"
            )
        return result

    def validate(self, pdf_path: str) -> VerificationOutcome:
        """Validates `pdf_path` against veraPDF's given profile (default: PDF/UA-1).

        Requests XML rather than JSON: veraPDF's own docs only document XML's schema
        (`isCompliant` on `<validationReport>`); its exit codes are documented for PDF/A
        specifically and, per an open, unresolved veraPDF GitHub issue
        (veraPDF/veraPDF-library#1562), the maintainers themselves aren't sure whether they
        generalize to other flavours like ua1 — so compliance is read from the parsed report,
        never inferred from the process's return code.

        `failed_rules` is parsed from `validationReport/details/rule` — confirmed against
        real veraPDF 1.30.2 output (simplify_vera_printouts), e.g.
        `<rule clause="7.21.7" failedChecks="44"><description>...</description>...</rule>`.
        `verapdf_version` comes from the same report's `buildInformation` block, so every
        result records the exact tool version that actually produced it.
        """
        result = self.run_vera_pdf(pdf_path)

        try:
            root = ElementTree.fromstring(result.stdout)
        except ElementTree.ParseError as exc:
            self.raise_adapter_error(f"verapdf produced unparseable XML: {exc}")

        validation_report = root.find("jobs/job/validationReport")
        if validation_report is None:
            self.raise_adapter_error(f"verapdf report had no validationReport: {result.stdout}")

        is_compliant = validation_report.get("isCompliant") == "true"
        failed_rules = []
        for rule in validation_report.findall("details/rule"):
            clause = rule.get("clause", "")
            test_number = rule.get("testNumber", "")
            failed_rules.append(
                FailedRule(
                    clause=clause,
                    test_number=test_number,
                    description=(rule.findtext("description") or "").strip(),
                    failed_checks=int(rule.get("failedChecks", 0)),
                    severity=classify(clause, test_number),
                )
            )

        core_release = root.find("buildInformation/releaseDetails[@id='core']")
        verapdf_version = core_release.get("version", "") if core_release is not None else ""

        return VerificationOutcome(
            is_compliant=is_compliant, failed_rules=failed_rules, verapdf_version=verapdf_version
        )
