from __future__ import annotations

import subprocess
from unittest.mock import patch

from django.test import SimpleTestCase
from parameterized import parameterized

from remediation.adapters.base import Adapter, AdapterError
from remediation.adapters.verification.vera_pdf import VeraPDFAdapter

COMPLIANT_REPORT = """<?xml version="1.0" encoding="utf-8"?>
<report>
  <jobs>
    <job>
      <validationReport isCompliant="true"></validationReport>
    </job>
  </jobs>
</report>
"""

NONCOMPLIANT_REPORT = """<?xml version="1.0" encoding="utf-8"?>
<report>
  <jobs>
    <job>
      <validationReport isCompliant="false"></validationReport>
    </job>
  </jobs>
</report>
"""


def _completed_process(
    *, returncode: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["verapdf"], returncode=returncode, stdout=stdout, stderr=stderr
    )


class _ConcreteAdapter(Adapter):
    """Minimal concrete `Adapter` — just enough to satisfy the ABC, so the shared
    `raise_adapter_error` path can be tested once, independent of any real adapter."""

    @property
    def name(self) -> str:
        return "concrete test adapter"


class AdapterTests(SimpleTestCase):
    def test_raise_adapter_error_raises_adapter_error_with_message(self) -> None:
        with self.assertRaisesMessage(AdapterError, "boom"):
            _ConcreteAdapter().raise_adapter_error("boom")


class VeraPDFAdapterTests(SimpleTestCase):
    @parameterized.expand(
        [
            ("compliant", COMPLIANT_REPORT, True),
            ("noncompliant", NONCOMPLIANT_REPORT, False),
        ]
    )
    @patch("remediation.adapters.verification.vera_pdf.subprocess.run", autospec=True)
    def test_validate_reads_compliance_from_report(
        self, _name, report_xml, expected, mock_run
    ) -> None:
        mock_run.return_value = _completed_process(returncode=0, stdout=report_xml)

        is_compliant, report = VeraPDFAdapter().validate("/tmp/document.pdf")

        self.assertEqual(is_compliant, expected)
        self.assertEqual(report, report_xml)

    @patch("remediation.adapters.verification.vera_pdf.subprocess.run", autospec=True)
    def test_validate_invokes_verapdf_with_flavour_and_xml_format(self, mock_run) -> None:
        mock_run.return_value = _completed_process(stdout=COMPLIANT_REPORT)

        VeraPDFAdapter(flavour="ua1").validate("/tmp/document.pdf")

        mock_run.assert_called_once_with(
            ["verapdf", "--flavour", "ua1", "--format", "xml", "/tmp/document.pdf"],
            capture_output=True,
            text=True,
        )

    @patch("remediation.adapters.verification.vera_pdf.subprocess.run", autospec=True)
    def test_validate_raises_when_verapdf_not_installed(self, mock_run) -> None:
        mock_run.side_effect = FileNotFoundError()

        with self.assertRaises(AdapterError):
            VeraPDFAdapter().validate("/tmp/document.pdf")

    @patch("remediation.adapters.verification.vera_pdf.subprocess.run", autospec=True)
    def test_validate_raises_for_empty_output(self, mock_run) -> None:
        mock_run.return_value = _completed_process(returncode=9, stdout="", stderr="boom")

        with self.assertRaises(AdapterError):
            VeraPDFAdapter().validate("/tmp/document.pdf")

    @patch("remediation.adapters.verification.vera_pdf.subprocess.run", autospec=True)
    def test_validate_raises_for_unparseable_xml(self, mock_run) -> None:
        mock_run.return_value = _completed_process(stdout="not xml")

        with self.assertRaises(AdapterError):
            VeraPDFAdapter().validate("/tmp/document.pdf")

    @patch("remediation.adapters.verification.vera_pdf.subprocess.run", autospec=True)
    def test_validate_raises_when_report_has_no_validation_report(self, mock_run) -> None:
        mock_run.return_value = _completed_process(stdout="<report><jobs></jobs></report>")

        with self.assertRaises(AdapterError):
            VeraPDFAdapter().validate("/tmp/document.pdf")
