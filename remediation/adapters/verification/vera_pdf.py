import subprocess
from xml.etree import ElementTree

from remediation.adapters.base import VerificationAdapter


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

    def validate(self, pdf_path: str) -> tuple[bool, str]:
        """Validates `pdf_path` against veraPDF's given profile (default: PDF/UA-1).

        Returns `(is_compliant, report_xml)`. Requests XML rather than JSON: veraPDF's own
        docs only document XML's schema (`isCompliant` on `<validationReport>`); its exit
        codes are documented for PDF/A specifically and, per an open, unresolved veraPDF
        GitHub issue (veraPDF/veraPDF-library#1562), the maintainers themselves aren't sure
        whether they generalize to other flavours like ua1 — so compliance is read from the
        parsed report, never inferred from the process's return code.
        """
        result = self.run_vera_pdf(pdf_path)

        try:
            root = ElementTree.fromstring(result.stdout)
        except ElementTree.ParseError as exc:
            self.raise_adapter_error(f"verapdf produced unparseable XML: {exc}")

        validation_report = root.find("jobs/job/validationReport")
        if validation_report is None:
            self.raise_adapter_error(f"verapdf report had no validationReport: {result.stdout}")

        return validation_report.get("isCompliant") == "true", result.stdout
