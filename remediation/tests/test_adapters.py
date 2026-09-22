from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

import pikepdf
import pymupdf as fitz
from django.test import SimpleTestCase
from parameterized import parameterized
from PIL import Image

from remediation.adapters.alt_text.claude_vision import ClaudeVisionClient
from remediation.adapters.alt_text.pike_pdf import (
    PikePdfAdapter as AltTextPikePdfAdapter,
)
from remediation.adapters.base import Adapter, AdapterError, FailedRule
from remediation.adapters.font_repair.pike_pdf import (
    PikePdfAdapter as FontRepairPikePdfAdapter,
)
from remediation.adapters.font_repair.pike_pdf import _build_tounicode_cmap
from remediation.adapters.link.pike_pdf import PikePdfAdapter as LinkPikePdfAdapter
from remediation.adapters.metadata.pike_pdf import PikePdfAdapter
from remediation.adapters.ocr.open_data_loader import OpenDataLoaderAdapter
from remediation.adapters.scoring.pike_pdf import (
    PikePdfAdapter as ScoringPikePdfAdapter,
)
from remediation.adapters.verification.severity import Severity
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

# Trimmed from a real veraPDF 1.30.2 run against
# remediation/tests/fixtures/edge-cases/page9-broken-tounicode-offset29.pdf — two failed rules
# across different clauses, each with its own `failedChecks` count.
MULTI_RULE_REPORT = """<?xml version="1.0" encoding="utf-8"?>
<report>
  <buildInformation>
    <releaseDetails id="core" version="1.30.2" buildDate="2026-06-03T10:43:00Z"></releaseDetails>
  </buildInformation>
  <jobs>
    <job>
      <validationReport isCompliant="false">
        <details passedRules="98" failedRules="2" passedChecks="829" failedChecks="45">
          <rule specification="ISO 14289-1:2014" clause="7.1" testNumber="11" status="failed" failedChecks="1" tags="structure">
            <description>StructTreeRoot entry is not present in the document catalog</description>
            <object>PDDocument</object>
            <test>containsStructTreeRoot == true</test>
            <check status="failed">
              <context>root/document[0]</context>
              <errorMessage>StructTreeRoot entry is not present in the document catalog</errorMessage>
            </check>
          </rule>
          <rule specification="ISO 14289-1:2014" clause="7.21.7" testNumber="1" status="failed" failedChecks="44" tags="font">
            <description>The glyph can not be mapped to Unicode</description>
            <object>Glyph</object>
            <test>toUnicode != null</test>
            <check status="failed">
              <context>root/.../usedGlyphs[0]</context>
              <errorMessage>The glyph can not be mapped to Unicode</errorMessage>
            </check>
          </rule>
        </details>
      </validationReport>
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

        outcome = VeraPDFAdapter().validate("/tmp/document.pdf")

        self.assertEqual(outcome.is_compliant, expected)
        self.assertEqual(outcome.failed_rules, [])

    @patch("remediation.adapters.verification.vera_pdf.subprocess.run", autospec=True)
    def test_validate_parses_failed_rules_across_different_clauses(self, mock_run) -> None:
        mock_run.return_value = _completed_process(stdout=MULTI_RULE_REPORT)

        outcome = VeraPDFAdapter().validate("/tmp/document.pdf")

        self.assertEqual(
            outcome.failed_rules,
            [
                FailedRule(
                    clause="7.1",
                    test_number="11",
                    description="StructTreeRoot entry is not present in the document catalog",
                    failed_checks=1,
                    severity=Severity.CRITICAL,
                ),
                FailedRule(
                    clause="7.21.7",
                    test_number="1",
                    description="The glyph can not be mapped to Unicode",
                    failed_checks=44,
                    severity=Severity.MINOR,
                ),
            ],
        )

    @patch("remediation.adapters.verification.vera_pdf.subprocess.run", autospec=True)
    def test_validate_parses_verapdf_version_from_build_information(self, mock_run) -> None:
        mock_run.return_value = _completed_process(stdout=MULTI_RULE_REPORT)

        outcome = VeraPDFAdapter().validate("/tmp/document.pdf")

        self.assertEqual(outcome.verapdf_version, "1.30.2")

    @patch("remediation.adapters.verification.vera_pdf.subprocess.run", autospec=True)
    def test_validate_defaults_verapdf_version_when_build_information_missing(
        self, mock_run
    ) -> None:
        mock_run.return_value = _completed_process(stdout=COMPLIANT_REPORT)

        outcome = VeraPDFAdapter().validate("/tmp/document.pdf")

        self.assertEqual(outcome.verapdf_version, "")

    @patch("remediation.adapters.verification.vera_pdf.subprocess.run", autospec=True)
    def test_validate_returns_empty_failed_rules_when_no_details_element(self, mock_run) -> None:
        mock_run.return_value = _completed_process(stdout=NONCOMPLIANT_REPORT)

        outcome = VeraPDFAdapter().validate("/tmp/document.pdf")

        self.assertEqual(outcome.failed_rules, [])

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


class OpenDataLoaderAdapterTests(SimpleTestCase):
    def setUp(self) -> None:
        self.output_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.output_dir, ignore_errors=True)

    def _write_output(self, name: str) -> str:
        path = os.path.join(self.output_dir, name)
        with open(path, "w") as f:
            f.write("fake pdf output")
        return path

    @patch("remediation.adapters.ocr.open_data_loader.opendataloader_pdf.convert", autospec=True)
    def test_extract_returns_path_to_matching_output_pdf(self, mock_convert) -> None:
        mock_convert.side_effect = lambda **kwargs: self._write_output("document.pdf")

        result = OpenDataLoaderAdapter().extract(
            "/tmp/input/document.pdf", output_dir=self.output_dir
        )

        self.assertEqual(result, os.path.join(self.output_dir, "document.pdf"))

    @patch("remediation.adapters.ocr.open_data_loader.opendataloader_pdf.convert", autospec=True)
    def test_extract_invokes_convert_with_hybrid_mode_and_tagged_pdf_format(
        self, mock_convert
    ) -> None:
        mock_convert.side_effect = lambda **kwargs: self._write_output("document.pdf")

        OpenDataLoaderAdapter().extract("/tmp/input/document.pdf", output_dir=self.output_dir)

        mock_convert.assert_called_once_with(
            input_path=["/tmp/input/document.pdf"],
            output_dir=self.output_dir,
            format="tagged-pdf",
            hybrid="docling-fast",
        )

    @patch("remediation.adapters.ocr.open_data_loader.opendataloader_pdf.convert", autospec=True)
    def test_extract_passes_hybrid_url_when_configured(self, mock_convert) -> None:
        mock_convert.side_effect = lambda **kwargs: self._write_output("document.pdf")

        OpenDataLoaderAdapter(hybrid_url="http://opendataloader-hybrid:5002").extract(
            "/tmp/input/document.pdf", output_dir=self.output_dir
        )

        mock_convert.assert_called_once_with(
            input_path=["/tmp/input/document.pdf"],
            output_dir=self.output_dir,
            format="tagged-pdf",
            hybrid="docling-fast",
            hybrid_url="http://opendataloader-hybrid:5002",
        )

    @patch("remediation.adapters.ocr.open_data_loader.opendataloader_pdf.convert", autospec=True)
    def test_extract_renames_output_to_match_input_stem(self, mock_convert) -> None:
        mock_convert.side_effect = lambda **kwargs: self._write_output("document_extracted.pdf")

        result = OpenDataLoaderAdapter().extract(
            "/tmp/input/document.pdf", output_dir=self.output_dir
        )

        expected_path = os.path.join(self.output_dir, "document.pdf")
        self.assertEqual(result, expected_path)
        self.assertTrue(os.path.exists(expected_path))
        self.assertFalse(os.path.exists(os.path.join(self.output_dir, "document_extracted.pdf")))

    @patch("remediation.adapters.ocr.open_data_loader.opendataloader_pdf.convert", autospec=True)
    def test_extract_raises_when_no_output_produced(self, mock_convert) -> None:
        with self.assertRaises(AdapterError):
            OpenDataLoaderAdapter().extract("/tmp/input/document.pdf", output_dir=self.output_dir)

    @patch("remediation.adapters.ocr.open_data_loader.opendataloader_pdf.convert", autospec=True)
    def test_extract_raises_when_multiple_candidate_outputs(self, mock_convert) -> None:
        def _write_two(**kwargs):
            self._write_output("document.pdf")
            self._write_output("document_2.pdf")

        mock_convert.side_effect = _write_two

        with self.assertRaises(AdapterError):
            OpenDataLoaderAdapter().extract("/tmp/input/document.pdf", output_dir=self.output_dir)

    @patch("remediation.adapters.ocr.open_data_loader.opendataloader_pdf.convert", autospec=True)
    def test_extract_wraps_underlying_exception_in_adapter_error(self, mock_convert) -> None:
        mock_convert.side_effect = Exception("java not found")

        with self.assertRaisesMessage(AdapterError, "opendataloader-pdf failed: java not found"):
            OpenDataLoaderAdapter().extract("/tmp/input/document.pdf", output_dir=self.output_dir)

    @patch("remediation.adapters.ocr.open_data_loader.opendataloader_pdf.convert", autospec=True)
    def test_extract_includes_stderr_from_called_process_error(self, mock_convert) -> None:
        mock_convert.side_effect = subprocess.CalledProcessError(
            returncode=1, cmd=["java", "-jar", "cli.jar"], stderr="Exception in thread main"
        )

        with self.assertRaisesMessage(AdapterError, "Exception in thread main"):
            OpenDataLoaderAdapter().extract("/tmp/input/document.pdf", output_dir=self.output_dir)

    @patch("remediation.adapters.ocr.open_data_loader.opendataloader_pdf.convert", autospec=True)
    def test_extract_decodes_bytes_stderr_from_called_process_error(self, mock_convert) -> None:
        mock_convert.side_effect = subprocess.CalledProcessError(
            returncode=1, cmd=["java", "-jar", "cli.jar"], stderr=b"Exception in thread main"
        )

        with self.assertRaisesMessage(AdapterError, "Exception in thread main"):
            OpenDataLoaderAdapter().extract("/tmp/input/document.pdf", output_dir=self.output_dir)


class PikePdfAdapterTests(SimpleTestCase):
    def setUp(self) -> None:
        self.input_dir = tempfile.mkdtemp()
        self.output_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.input_dir, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.output_dir, ignore_errors=True)

    def _write_minimal_pdf(self, name: str = "document.pdf") -> str:
        path = os.path.join(self.input_dir, name)
        pdf = pikepdf.new()
        pdf.add_blank_page(page_size=(200, 200))
        pdf.save(path)
        return path

    def test_finalize_writes_output_under_input_basename(self) -> None:
        input_path = self._write_minimal_pdf("document.pdf")

        result = PikePdfAdapter().finalize(
            input_path, output_dir=self.output_dir, title="A Title", lang="en-us"
        )

        self.assertEqual(result, os.path.join(self.output_dir, "document.pdf"))
        self.assertTrue(os.path.exists(result))

    def test_finalize_sets_mark_info_lang_title_and_tab_order(self) -> None:
        input_path = self._write_minimal_pdf()

        result = PikePdfAdapter().finalize(
            input_path, output_dir=self.output_dir, title="A Title", lang="en-us"
        )

        with pikepdf.open(result) as pdf:
            self.assertTrue(pdf.Root.MarkInfo.Marked)
            self.assertEqual(str(pdf.Root.Lang), "en-us")
            self.assertTrue(pdf.Root.ViewerPreferences.DisplayDocTitle)
            with pdf.open_metadata() as meta:
                self.assertEqual(meta["dc:title"], "A Title")
                self.assertEqual(meta["pdfuaid:part"], "1")
            for page in pdf.pages:
                self.assertEqual(str(page["/Tabs"]), "/S")

    def test_finalize_raises_adapter_error_for_unopenable_pdf(self) -> None:
        bad_path = os.path.join(self.input_dir, "not-a-pdf.pdf")
        with open(bad_path, "w") as f:
            f.write("not a pdf")

        with self.assertRaises(AdapterError):
            PikePdfAdapter().finalize(
                bad_path, output_dir=self.output_dir, title="A Title", lang="en-us"
            )


class LinkAdapterTests(SimpleTestCase):
    def setUp(self) -> None:
        self.input_dir = tempfile.mkdtemp()
        self.output_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.input_dir, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.output_dir, ignore_errors=True)

    def _write_pdf_with_link(self, name: str = "document.pdf", *, tagged: bool = True) -> str:
        path = os.path.join(self.input_dir, name)
        pdf = pikepdf.new()
        page = pdf.add_blank_page(page_size=(200, 200))

        link_annot = pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name("/Annot"),
                Subtype=pikepdf.Name("/Link"),
                Rect=pikepdf.Array([0, 0, 10, 10]),
            )
        )
        page["/Annots"] = pikepdf.Array([link_annot])

        if tagged:
            pdf.Root.StructTreeRoot = pdf.make_indirect(
                pikepdf.Dictionary(Type=pikepdf.Name("/StructTreeRoot"), K=pikepdf.Array())
            )

        pdf.save(path)
        return path

    def test_repair_writes_output_under_input_basename(self) -> None:
        input_path = self._write_pdf_with_link()

        result = LinkPikePdfAdapter().repair(input_path, output_dir=self.output_dir)

        self.assertEqual(result, os.path.join(self.output_dir, "document.pdf"))
        self.assertTrue(os.path.exists(result))

    def test_repair_creates_link_struct_element_for_untagged_annotation(self) -> None:
        input_path = self._write_pdf_with_link()

        result = LinkPikePdfAdapter().repair(input_path, output_dir=self.output_dir)

        with pikepdf.open(result) as pdf:
            kids = list(pdf.Root.StructTreeRoot.K)
            self.assertEqual(len(kids), 1)
            link_elem = kids[0]
            self.assertEqual(str(link_elem.S), "/Link")
            objr = list(link_elem.K)[0]
            self.assertEqual(str(objr.Type), "/OBJR")
            self.assertEqual(str(objr.Obj.Subtype), "/Link")

    def test_repair_passes_through_unchanged_when_no_struct_tree_root(self) -> None:
        input_path = self._write_pdf_with_link(tagged=False)

        result = LinkPikePdfAdapter().repair(input_path, output_dir=self.output_dir)

        with pikepdf.open(result) as pdf:
            self.assertNotIn("/StructTreeRoot", pdf.Root)

    def test_repair_is_idempotent_on_already_tagged_annotation(self) -> None:
        input_path = self._write_pdf_with_link()
        adapter = LinkPikePdfAdapter()
        second_output_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, second_output_dir, ignore_errors=True)

        first_output = adapter.repair(input_path, output_dir=self.output_dir)
        second_output = adapter.repair(first_output, output_dir=second_output_dir)

        with pikepdf.open(second_output) as pdf:
            kids = list(pdf.Root.StructTreeRoot.K)
            self.assertEqual(len(kids), 1)

    def test_repair_ignores_non_link_annotations(self) -> None:
        path = os.path.join(self.input_dir, "document.pdf")
        pdf = pikepdf.new()
        page = pdf.add_blank_page(page_size=(200, 200))
        link_annot = pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name("/Annot"),
                Subtype=pikepdf.Name("/Link"),
                Rect=pikepdf.Array([0, 0, 10, 10]),
            )
        )
        widget_annot = pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name("/Annot"),
                Subtype=pikepdf.Name("/Widget"),
                Rect=pikepdf.Array([0, 0, 10, 10]),
            )
        )
        page["/Annots"] = pikepdf.Array([link_annot, widget_annot])
        pdf.Root.StructTreeRoot = pdf.make_indirect(
            pikepdf.Dictionary(Type=pikepdf.Name("/StructTreeRoot"), K=pikepdf.Array())
        )
        pdf.save(path)

        result = LinkPikePdfAdapter().repair(path, output_dir=self.output_dir)

        with pikepdf.open(result) as out_pdf:
            kids = list(out_pdf.Root.StructTreeRoot.K)
            self.assertEqual(len(kids), 1)
            self.assertEqual(str(kids[0].S), "/Link")

    def test_repair_raises_adapter_error_for_unopenable_pdf(self) -> None:
        bad_path = os.path.join(self.input_dir, "not-a-pdf.pdf")
        with open(bad_path, "w") as f:
            f.write("not a pdf")

        with self.assertRaises(AdapterError):
            LinkPikePdfAdapter().repair(bad_path, output_dir=self.output_dir)


class FontRepairAdapterTests(SimpleTestCase):
    """The ToUnicode-repair tests below use synthetic Type0/Identity-H font dictionaries with
    no real `FontFile` — the adapter never inspects the font program for that repair, only the
    dictionary structure and the content stream's shown codes. The CIDSet-repair tests further
    down (`FontRepairAdapterCidSetTests`) *do* need a real, minimal embedded `FontFile2`, built
    with `fontTools.fontBuilder` — that repair reads the font program's actual glyph count.
    """

    def setUp(self) -> None:
        self.input_dir = tempfile.mkdtemp()
        self.output_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.input_dir, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.output_dir, ignore_errors=True)

    def _identity_h_font(
        self,
        pdf: pikepdf.Pdf,
        *,
        tounicode: bool = False,
        descendant_subtype: str = "/CIDFontType2",
        encoding: str = "/Identity-H",
        base_font: str = "/Test+Synthetic",
    ) -> pikepdf.Object:
        descendant = pikepdf.Dictionary(
            Type=pikepdf.Name("/Font"),
            Subtype=pikepdf.Name(descendant_subtype),
            BaseFont=pikepdf.Name(base_font),
            CIDSystemInfo=pikepdf.Dictionary(Registry="Adobe", Ordering="Identity", Supplement=0),
            CIDToGIDMap=pikepdf.Name("/Identity"),
        )
        font = pikepdf.Dictionary(
            Type=pikepdf.Name("/Font"),
            Subtype=pikepdf.Name("/Type0"),
            BaseFont=pikepdf.Name(base_font),
            Encoding=pikepdf.Name(encoding),
            DescendantFonts=pikepdf.Array([pdf.make_indirect(descendant)]),
        )
        if tounicode:
            font["/ToUnicode"] = pdf.make_indirect(pikepdf.Stream(pdf, b"already present"))
        return pdf.make_indirect(font)

    def _write_pdf_with_font(
        self,
        name: str,
        *,
        text: str,
        offset: int,
        tounicode: bool = False,
        descendant_subtype: str = "/CIDFontType2",
    ) -> str:
        """Writes a page whose content stream shows `text` via a synthetic Identity-H font,
        with each character's code shifted by `-offset` — so the correct recovery offset for
        the adapter to find is exactly `offset`.
        """
        path = os.path.join(self.input_dir, name)
        pdf = pikepdf.new()
        page = pdf.add_blank_page(page_size=(400, 200))
        page["/Resources"] = pikepdf.Dictionary(
            Font=pikepdf.Dictionary(
                F1=self._identity_h_font(
                    pdf, tounicode=tounicode, descendant_subtype=descendant_subtype
                )
            )
        )
        hex_codes = "".join(f"{ord(c) - offset:04X}" for c in text)
        content = f"BT /F1 12 Tf <{hex_codes}> Tj ET".encode("ascii")
        page["/Contents"] = pdf.make_indirect(pikepdf.Stream(pdf, content))
        pdf.save(path)
        return path

    def test_repair_writes_output_under_input_basename(self) -> None:
        input_path = self._write_pdf_with_font(
            "document.pdf", text="the department office please", offset=17
        )

        result = FontRepairPikePdfAdapter().repair(input_path, output_dir=self.output_dir)

        self.assertEqual(result, os.path.join(self.output_dir, "document.pdf"))
        self.assertTrue(os.path.exists(result))

    def test_repair_recovers_tounicode_for_offset_encoded_font(self) -> None:
        # A phrase built entirely from the adapter's bundled common-word list, long enough to
        # clear MIN_USED_CODES — matches the real, confirmed repro (constant per-font offset,
        # recovered via printable + word-hit coherence scoring), not a hand-picked shortcut.
        text = "please prepare the department form for the county board and file the request"
        input_path = self._write_pdf_with_font("document.pdf", text=text, offset=29)

        result = FontRepairPikePdfAdapter().repair(input_path, output_dir=self.output_dir)

        with pikepdf.open(result) as pdf:
            font = pdf.pages[0].obj["/Resources"]["/Font"]["/F1"]
            self.assertIn("/ToUnicode", font)
            cmap_bytes = bytes(font["/ToUnicode"].read_bytes())
            # Every code shown in the content stream should decode to its correct character.
            for char in text:
                code = ord(char) - 29
                self.assertIn(f"<{code:04X}> <{ord(char):04X}>".upper(), cmap_bytes.decode())

    def test_repair_leaves_font_untouched_when_already_has_tounicode(self) -> None:
        input_path = self._write_pdf_with_font(
            "document.pdf",
            text="please prepare the department form for the county board",
            offset=29,
            tounicode=True,
        )

        result = FontRepairPikePdfAdapter().repair(input_path, output_dir=self.output_dir)

        with pikepdf.open(result) as pdf:
            font = pdf.pages[0].obj["/Resources"]["/Font"]["/F1"]
            self.assertEqual(bytes(font["/ToUnicode"].read_bytes()), b"already present")

    def test_repair_leaves_font_untouched_when_no_confident_offset(self) -> None:
        # Random, non-language bytes — no constant offset should score above the confidence
        # threshold, so the font must be left exactly as it started: no fabricated mapping.
        path = os.path.join(self.input_dir, "document.pdf")
        pdf = pikepdf.new()
        page = pdf.add_blank_page(page_size=(400, 200))
        page["/Resources"] = pikepdf.Dictionary(
            Font=pikepdf.Dictionary(F1=self._identity_h_font(pdf))
        )
        # Codes chosen to be spread out and non-sequential — not derivable from any single
        # constant shift of real English text.
        codes = [0x1F3A, 0x0042, 0x7E01, 0x3C9B, 0x0511, 0x22F4, 0x6BAA, 0x0190, 0x4471, 0x2E0C]
        hex_codes = "".join(f"{c:04X}" for c in codes)
        content = f"BT /F1 12 Tf <{hex_codes}> Tj ET".encode("ascii")
        page["/Contents"] = pdf.make_indirect(pikepdf.Stream(pdf, content))
        pdf.save(path)

        result = FontRepairPikePdfAdapter().repair(path, output_dir=self.output_dir)

        with pikepdf.open(result) as out_pdf:
            font = out_pdf.pages[0].obj["/Resources"]["/Font"]["/F1"]
            self.assertNotIn("/ToUnicode", font)

    def test_repair_ignores_non_identity_cid_fonts(self) -> None:
        path = os.path.join(self.input_dir, "document.pdf")
        pdf = pikepdf.new()
        page = pdf.add_blank_page(page_size=(400, 200))
        simple_font = pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name("/Font"),
                Subtype=pikepdf.Name("/TrueType"),
                BaseFont=pikepdf.Name("/Helvetica"),
                Encoding=pikepdf.Name("/WinAnsiEncoding"),
            )
        )
        page["/Resources"] = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=simple_font))
        page["/Contents"] = pdf.make_indirect(pikepdf.Stream(pdf, b"BT /F1 12 Tf (Hello) Tj ET"))
        pdf.save(path)

        result = FontRepairPikePdfAdapter().repair(path, output_dir=self.output_dir)

        with pikepdf.open(result) as out_pdf:
            font = out_pdf.pages[0].obj["/Resources"]["/Font"]["/F1"]
            self.assertNotIn("/ToUnicode", font)

    def test_repair_raises_adapter_error_for_unopenable_pdf(self) -> None:
        bad_path = os.path.join(self.input_dir, "not-a-pdf.pdf")
        with open(bad_path, "w") as f:
            f.write("not a pdf")

        with self.assertRaises(AdapterError):
            FontRepairPikePdfAdapter().repair(bad_path, output_dir=self.output_dir)

    def test_repair_recovers_tounicode_for_cidfonttype0_descendant(self) -> None:
        # _is_candidate explicitly accepts CFF-based CIDFontType0 too (confirmed real case:
        # CANKNL+HelveticaNeueLTStd-Lt), not just the TrueType CIDFontType2 every other test
        # here uses.
        text = "please prepare the department form for the county board and file the request"
        input_path = self._write_pdf_with_font(
            "document.pdf", text=text, offset=29, descendant_subtype="/CIDFontType0"
        )

        result = FontRepairPikePdfAdapter().repair(input_path, output_dir=self.output_dir)

        with pikepdf.open(result) as pdf:
            font = pdf.pages[0].obj["/Resources"]["/Font"]["/F1"]
            self.assertIn("/ToUnicode", font)

    def test_repair_recovers_tounicode_from_tj_array_operator(self) -> None:
        # TJ (array show with kerning adjustments) is how most real PDFs render justified
        # text — numeric kerning operands must be skipped, not mistaken for codes.
        path = os.path.join(self.input_dir, "document.pdf")
        pdf = pikepdf.new()
        page = pdf.add_blank_page(page_size=(400, 200))
        page["/Resources"] = pikepdf.Dictionary(
            Font=pikepdf.Dictionary(F1=self._identity_h_font(pdf))
        )
        text = "please prepare the department form for the county board and file the request"
        offset = 29
        mid = len(text) // 2
        hex_first = "".join(f"{ord(c) - offset:04X}" for c in text[:mid])
        hex_second = "".join(f"{ord(c) - offset:04X}" for c in text[mid:])
        content = f"BT /F1 12 Tf [<{hex_first}> -120 <{hex_second}>] TJ ET".encode("ascii")
        page["/Contents"] = pdf.make_indirect(pikepdf.Stream(pdf, content))
        pdf.save(path)

        result = FontRepairPikePdfAdapter().repair(path, output_dir=self.output_dir)

        with pikepdf.open(result) as out_pdf:
            font = out_pdf.pages[0].obj["/Resources"]["/Font"]["/F1"]
            self.assertIn("/ToUnicode", font)
            cmap_bytes = bytes(font["/ToUnicode"].read_bytes()).decode()
            for char in text:
                code = ord(char) - offset
                self.assertIn(f"<{code:04X}> <{ord(char):04X}>".upper(), cmap_bytes)

    def test_repair_recovers_wrong_case_when_words_are_split_across_show_operations(self) -> None:
        # Real, confirmed finding: when text is shown via several separate Tj calls (the normal
        # shape of a real content stream — one call per word/line) rather than one blob with
        # literal encoded spaces, the adapter's `None`-as-space boundary marker is offset-
        # invariant (it's always rendered as a literal space regardless of which offset is being
        # scored). That removes the printable-ratio gate's only way to disambiguate the true
        # offset from `offset - 32`, which decodes the same letters to their uppercase form —
        # and since word-hit matching is case-insensitive, both score identically. The scan
        # order (`range(-255, 256)`, ties won by the first/smaller candidate) then picks
        # `offset - 32` over the true offset. The result: codes get mapped to the UPPERCASE of
        # the character actually shown, not the lowercase original — silently wrong-case
        # extracted text, not a rejection. This documents that real behavior.
        path = os.path.join(self.input_dir, "document.pdf")
        pdf = pikepdf.new()
        page = pdf.add_blank_page(page_size=(400, 200))
        page["/Resources"] = pikepdf.Dictionary(
            Font=pikepdf.Dictionary(F1=self._identity_h_font(pdf))
        )
        offset = 29
        words = ["please", "prepare", "the", "department", "form", "for", "the", "county", "board"]

        def hex_for(word: str) -> str:
            return "".join(f"{ord(c) - offset:04X}" for c in word)

        show_ops = " ".join(f"<{hex_for(w)}> Tj" for w in words)
        content = f"BT /F1 12 Tf {show_ops} ET".encode("ascii")
        page["/Contents"] = pdf.make_indirect(pikepdf.Stream(pdf, content))
        pdf.save(path)

        result = FontRepairPikePdfAdapter().repair(path, output_dir=self.output_dir)

        with pikepdf.open(result) as out_pdf:
            font = out_pdf.pages[0].obj["/Resources"]["/Font"]["/F1"]
            self.assertIn("/ToUnicode", font)
            cmap_bytes = bytes(font["/ToUnicode"].read_bytes()).decode()
            for char in "".join(words):
                code = ord(char) - offset
                self.assertNotIn(f"<{code:04X}> <{ord(char):04X}>".upper(), cmap_bytes)
                self.assertIn(f"<{code:04X}> <{ord(char.upper()):04X}>".upper(), cmap_bytes)

    def test_repair_tounicode_coverage_for_font_shared_across_pages(self) -> None:
        # Real, confirmed finding: a subsetted embedded font is normally referenced from many
        # pages' /Resources (e.g. Arial-BoldMT on both page 9 and page 34 of the real BOC
        # packet this adapter was built for). `_repair_page_fonts` mutates the font dict in
        # place per page; once page 1's repair attaches /ToUnicode, `_is_candidate` rejects the
        # SAME font object on page 2 (it now "already has" ToUnicode) — so page 2's codes are
        # never looked at. This documents the actual result: page 1's codes are mapped
        # correctly, but codes used only on page 2 get no /ToUnicode entry at all.
        offset = 29
        page1_text = "please prepare the department form for the county board"
        page2_text = "the county resolution was approved and the motion passed"

        def hex_for(text: str) -> str:
            return "".join(f"{ord(c) - offset:04X}" for c in text)

        path = os.path.join(self.input_dir, "document.pdf")
        pdf = pikepdf.new()
        shared_font = self._identity_h_font(pdf)

        page1 = pdf.add_blank_page(page_size=(400, 200))
        page1["/Resources"] = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=shared_font))
        page1["/Contents"] = pdf.make_indirect(
            pikepdf.Stream(pdf, f"BT /F1 12 Tf <{hex_for(page1_text)}> Tj ET".encode("ascii"))
        )

        page2 = pdf.add_blank_page(page_size=(400, 200))
        page2["/Resources"] = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=shared_font))
        page2["/Contents"] = pdf.make_indirect(
            pikepdf.Stream(pdf, f"BT /F1 12 Tf <{hex_for(page2_text)}> Tj ET".encode("ascii"))
        )
        pdf.save(path)

        result = FontRepairPikePdfAdapter().repair(path, output_dir=self.output_dir)

        with pikepdf.open(result) as out_pdf:
            font = out_pdf.pages[0].obj["/Resources"]["/Font"]["/F1"]
            cmap_bytes = bytes(font["/ToUnicode"].read_bytes()).decode()
            page1_chars = set(page1_text) - {" "}
            page2_only_chars = (set(page2_text) - {" "}) - page1_chars
            for char in page1_chars:
                code = ord(char) - offset
                self.assertIn(f"<{code:04X}> <{ord(char):04X}>".upper(), cmap_bytes)
            for char in page2_only_chars:
                code = ord(char) - offset
                self.assertNotIn(f"<{code:04X}>".upper(), cmap_bytes)

    def test_repair_recovers_two_distinct_fonts_on_one_page_independently(self) -> None:
        offset1, offset2 = 29, 5
        text1 = "please prepare the department form for the county board and file the request"
        text2 = "the board approved the resolution and the motion for the county office today"

        def hex_for(text: str, offset: int) -> str:
            return "".join(f"{ord(c) - offset:04X}" for c in text)

        path = os.path.join(self.input_dir, "document.pdf")
        pdf = pikepdf.new()
        page = pdf.add_blank_page(page_size=(400, 200))
        page["/Resources"] = pikepdf.Dictionary(
            Font=pikepdf.Dictionary(
                F1=self._identity_h_font(pdf, base_font="/FontA"),
                F2=self._identity_h_font(pdf, base_font="/FontB"),
            )
        )
        content = (
            f"BT /F1 12 Tf <{hex_for(text1, offset1)}> Tj ET "
            f"BT /F2 12 Tf <{hex_for(text2, offset2)}> Tj ET"
        ).encode("ascii")
        page["/Contents"] = pdf.make_indirect(pikepdf.Stream(pdf, content))
        pdf.save(path)

        result = FontRepairPikePdfAdapter().repair(path, output_dir=self.output_dir)

        with pikepdf.open(result) as out_pdf:
            font1 = out_pdf.pages[0].obj["/Resources"]["/Font"]["/F1"]
            font2 = out_pdf.pages[0].obj["/Resources"]["/Font"]["/F2"]
            cmap1 = bytes(font1["/ToUnicode"].read_bytes()).decode()
            cmap2 = bytes(font2["/ToUnicode"].read_bytes()).decode()
            for char in text1:
                code = ord(char) - offset1
                self.assertIn(f"<{code:04X}> <{ord(char):04X}>".upper(), cmap1)
            for char in text2:
                code = ord(char) - offset2
                self.assertIn(f"<{code:04X}> <{ord(char):04X}>".upper(), cmap2)

    def test_repair_ignores_type0_font_with_non_identity_encoding(self) -> None:
        input_path = self._write_pdf_with_font(
            "document.pdf",
            text="please prepare the department form for the county board and file the request",
            offset=29,
        )
        # Rewrite the candidate font's /Encoding to a named (non-Identity) CMap after the fact —
        # simplest way to isolate just this one `_is_candidate` branch.
        with pikepdf.open(input_path, allow_overwriting_input=True) as pdf:
            font = pdf.pages[0].obj["/Resources"]["/Font"]["/F1"]
            font["/Encoding"] = pikepdf.Name("/WinAnsiEncoding")
            pdf.save(input_path)

        result = FontRepairPikePdfAdapter().repair(input_path, output_dir=self.output_dir)

        with pikepdf.open(result) as out_pdf:
            font = out_pdf.pages[0].obj["/Resources"]["/Font"]["/F1"]
            self.assertNotIn("/ToUnicode", font)

    def test_repair_ignores_type0_font_with_missing_descendant_fonts(self) -> None:
        path = os.path.join(self.input_dir, "document.pdf")
        pdf = pikepdf.new()
        page = pdf.add_blank_page(page_size=(400, 200))
        font = pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name("/Font"),
                Subtype=pikepdf.Name("/Type0"),
                BaseFont=pikepdf.Name("/Test"),
                Encoding=pikepdf.Name("/Identity-H"),
            )
        )
        page["/Resources"] = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
        text = "please prepare the department form for the county board and file the request"
        hex_codes = "".join(f"{ord(c) - 29:04X}" for c in text)
        page["/Contents"] = pdf.make_indirect(
            pikepdf.Stream(pdf, f"BT /F1 12 Tf <{hex_codes}> Tj ET".encode("ascii"))
        )
        pdf.save(path)

        result = FontRepairPikePdfAdapter().repair(path, output_dir=self.output_dir)

        with pikepdf.open(result) as out_pdf:
            font = out_pdf.pages[0].obj["/Resources"]["/Font"]["/F1"]
            self.assertNotIn("/ToUnicode", font)

    def test_repair_handles_page_with_no_resources(self) -> None:
        path = os.path.join(self.input_dir, "document.pdf")
        pdf = pikepdf.new()
        pdf.add_blank_page(page_size=(400, 200))
        pdf.save(path)

        result = FontRepairPikePdfAdapter().repair(path, output_dir=self.output_dir)

        self.assertTrue(os.path.exists(result))

    def test_repair_handles_page_with_resources_but_no_font(self) -> None:
        path = os.path.join(self.input_dir, "document.pdf")
        pdf = pikepdf.new()
        page = pdf.add_blank_page(page_size=(400, 200))
        page["/Resources"] = pikepdf.Dictionary()
        pdf.save(path)

        result = FontRepairPikePdfAdapter().repair(path, output_dir=self.output_dir)

        self.assertTrue(os.path.exists(result))

    def test_repair_returns_before_parsing_content_stream_when_no_candidates(self) -> None:
        # No candidate fonts on the page — `_repair_page_fonts` should return before even
        # attempting to parse the content stream, so a garbage stream here must not raise.
        path = os.path.join(self.input_dir, "document.pdf")
        pdf = pikepdf.new()
        page = pdf.add_blank_page(page_size=(400, 200))
        simple_font = pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name("/Font"),
                Subtype=pikepdf.Name("/TrueType"),
                BaseFont=pikepdf.Name("/Helvetica"),
            )
        )
        page["/Resources"] = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=simple_font))
        page["/Contents"] = pdf.make_indirect(pikepdf.Stream(pdf, b"\xff\xfe garbage not valid"))
        pdf.save(path)

        result = FontRepairPikePdfAdapter().repair(path, output_dir=self.output_dir)

        self.assertTrue(os.path.exists(result))

    def test_repair_raises_for_page_with_malformed_content_stream_even_when_other_pages_are_valid(
        self,
    ) -> None:
        # Real, confirmed finding: `_repair_page_fonts`'s try/except only wraps the
        # `parse_content_stream()` call itself (its own comment says "leave fonts untouched"),
        # but `parse_content_stream` is lazy — the actual decode error surfaces while iterating
        # tokens, outside that try/except. That exception propagates all the way out of
        # `repair()`'s per-page loop and is converted to an AdapterError, aborting repair for
        # the WHOLE document — not just skipping the one bad page, despite what the code's own
        # comment says. This documents the actual (not the intended) behavior.
        path = os.path.join(self.input_dir, "document.pdf")
        pdf = pikepdf.new()
        page1 = pdf.add_blank_page(page_size=(400, 200))
        page1["/Resources"] = pikepdf.Dictionary(
            Font=pikepdf.Dictionary(F1=self._identity_h_font(pdf, base_font="/FontC1"))
        )
        page1["/Contents"] = pdf.make_indirect(
            pikepdf.Stream(pdf, b"\xff\xfe\x00garbage(not valid content")
        )
        page2 = pdf.add_blank_page(page_size=(400, 200))
        page2["/Resources"] = pikepdf.Dictionary(
            Font=pikepdf.Dictionary(F1=self._identity_h_font(pdf, base_font="/FontC2"))
        )
        text = "please prepare the department form for the county board and file the request"
        hex_codes = "".join(f"{ord(c) - 29:04X}" for c in text)
        page2["/Contents"] = pdf.make_indirect(
            pikepdf.Stream(pdf, f"BT /F1 12 Tf <{hex_codes}> Tj ET".encode("ascii"))
        )
        pdf.save(path)

        with self.assertRaises(AdapterError):
            FontRepairPikePdfAdapter().repair(path, output_dir=self.output_dir)

    def test_repair_leaves_genuinely_unembedded_font_untouched(self) -> None:
        # Ties back to the diagnosed production failure (remediation 554fcae2-...): a simple
        # font with NO /FontFile, /FontFile2, or /FontFile3 at all is out of scope for this
        # adapter by design — it only repairs metadata on already-embedded fonts. This is a
        # living regression guard for that documented scope boundary, not an implicit side
        # effect of another test.
        path = os.path.join(self.input_dir, "document.pdf")
        pdf = pikepdf.new()
        page = pdf.add_blank_page(page_size=(400, 200))
        descriptor = pikepdf.Dictionary(
            Type=pikepdf.Name("/FontDescriptor"), FontName=pikepdf.Name("/ArialMT")
        )
        simple_font = pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name("/Font"),
                Subtype=pikepdf.Name("/TrueType"),
                BaseFont=pikepdf.Name("/ArialMT"),
                Encoding=pikepdf.Name("/WinAnsiEncoding"),
                FontDescriptor=pdf.make_indirect(descriptor),
            )
        )
        page["/Resources"] = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=simple_font))
        page["/Contents"] = pdf.make_indirect(pikepdf.Stream(pdf, b"BT /F1 12 Tf (Hello) Tj ET"))
        pdf.save(path)

        result = FontRepairPikePdfAdapter().repair(path, output_dir=self.output_dir)

        with pikepdf.open(result) as out_pdf:
            font_descriptor = out_pdf.pages[0].obj["/Resources"]["/Font"]["/F1"]["/FontDescriptor"]
            self.assertNotIn("/FontFile", font_descriptor)
            self.assertNotIn("/FontFile2", font_descriptor)
            self.assertNotIn("/FontFile3", font_descriptor)

    def test_repair_leaves_font_untouched_for_printable_but_non_word_codes(self) -> None:
        # The closest unit-test analog to the real KEEFEM+SymbolMT production failure: codes
        # that decode to printable characters under some offset (clearing the printable-ratio
        # gate) but never form recognizable common words (failing the word-hit gate) — distinct
        # from `test_repair_leaves_font_untouched_when_no_confident_offset`, which uses codes
        # that aren't reliably printable under any offset at all.
        path = os.path.join(self.input_dir, "document.pdf")
        pdf = pikepdf.new()
        page = pdf.add_blank_page(page_size=(400, 200))
        page["/Resources"] = pikepdf.Dictionary(
            Font=pikepdf.Dictionary(F1=self._identity_h_font(pdf))
        )
        offset = 20
        symbol_text = "zqx vbn wkl jft hrp myc"
        hex_codes = "".join(f"{ord(c) - offset:04X}" for c in symbol_text)
        content = f"BT /F1 12 Tf <{hex_codes}> Tj ET".encode("ascii")
        page["/Contents"] = pdf.make_indirect(pikepdf.Stream(pdf, content))
        pdf.save(path)

        result = FontRepairPikePdfAdapter().repair(path, output_dir=self.output_dir)

        with pikepdf.open(result) as out_pdf:
            font = out_pdf.pages[0].obj["/Resources"]["/Font"]["/F1"]
            self.assertNotIn("/ToUnicode", font)


class FontRepairToUnicodeCmapBuilderTests(SimpleTestCase):
    """Direct unit tests of `_build_tounicode_cmap`, the pure CMap-building function — bypasses
    `_best_offset`'s own printable/word-hit gates entirely, since those gates cap any single
    real recovered font at well under 100 distinct codes (the codespace of printable output
    characters), making the >100-code chunking path unreachable through the full `repair()`
    pipeline in a realistic fixture.
    """

    def test_chunks_beyond_the_100_code_beginbfchar_block_limit(self) -> None:
        codes = list(range(0x2000, 0x2000 + 150))

        cmap_bytes = _build_tounicode_cmap(codes, offset=1)

        cmap_text = cmap_bytes.decode()
        self.assertEqual(cmap_text.count("beginbfchar"), 2)
        self.assertEqual(cmap_text.count("endbfchar"), 2)
        self.assertIn(f"<{codes[0]:04X}> <{codes[0] + 1:04X}>".upper(), cmap_text)
        self.assertIn(f"<{codes[-1]:04X}> <{codes[-1] + 1:04X}>".upper(), cmap_text)

    def test_filters_out_codes_whose_shifted_target_is_outside_the_bmp(self) -> None:
        codes = [0xFFFE, 0xFFFF]

        cmap_bytes = _build_tounicode_cmap(codes, offset=2)

        cmap_text = cmap_bytes.decode()
        self.assertNotIn("beginbfchar", cmap_text)


def _minimal_ttf_bytes(num_glyphs: int) -> bytes:
    """Builds a tiny, real, valid TrueType font program with exactly `num_glyphs` glyphs
    (including `.notdef`) — enough for `fontTools.ttLib.TTFont` to read back a real
    `numGlyphs`, which is what the CIDSet repair actually reads. Real font bytes, not a mock,
    since the repair's whole job is reading the font program truthfully.
    """
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    pen = TTGlyphPen(None)
    pen.moveTo((0, 0))
    pen.lineTo((0, 500))
    pen.lineTo((500, 500))
    pen.closePath()
    glyph = pen.glyph()

    glyph_order = [".notdef"] + [f"glyph{i}" for i in range(1, num_glyphs)]
    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder(glyph_order)
    fb.setupCharacterMap({})
    fb.setupGlyf({name: glyph for name in glyph_order})
    fb.setupHorizontalMetrics({name: (500, 0) for name in glyph_order})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "Test", "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost()

    buf = io.BytesIO()
    fb.save(buf)
    return buf.getvalue()


def _pack_cidset(set_cids: set[int], num_bytes: int) -> bytes:
    result = bytearray(num_bytes)
    for cid in set_cids:
        byte_index, bit_index = divmod(cid, 8)
        result[byte_index] |= 1 << (7 - bit_index)
    return bytes(result)


class FontRepairAdapterCidSetTests(SimpleTestCase):
    def setUp(self) -> None:
        self.input_dir = tempfile.mkdtemp()
        self.output_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.input_dir, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.output_dir, ignore_errors=True)

    def _write_pdf_with_cid_font(
        self,
        name: str = "document.pdf",
        *,
        num_glyphs: int = 5,
        cidset_bytes: bytes | None,
        no_fontfile: bool = False,
    ) -> str:
        path = os.path.join(self.input_dir, name)
        pdf = pikepdf.new()
        page = pdf.add_blank_page(page_size=(400, 200))

        descriptor = pikepdf.Dictionary(
            Type=pikepdf.Name("/FontDescriptor"), FontName=pikepdf.Name("/Test+Synthetic")
        )
        if not no_fontfile:
            font_data = _minimal_ttf_bytes(num_glyphs)
            descriptor["/FontFile2"] = pdf.make_indirect(pikepdf.Stream(pdf, font_data))
        if cidset_bytes is not None:
            descriptor["/CIDSet"] = pdf.make_indirect(pikepdf.Stream(pdf, cidset_bytes))

        descendant = pikepdf.Dictionary(
            Type=pikepdf.Name("/Font"),
            Subtype=pikepdf.Name("/CIDFontType2"),
            BaseFont=pikepdf.Name("/Test+Synthetic"),
            CIDSystemInfo=pikepdf.Dictionary(Registry="Adobe", Ordering="Identity", Supplement=0),
            CIDToGIDMap=pikepdf.Name("/Identity"),
            FontDescriptor=pdf.make_indirect(descriptor),
        )
        font = pikepdf.Dictionary(
            Type=pikepdf.Name("/Font"),
            Subtype=pikepdf.Name("/Type0"),
            BaseFont=pikepdf.Name("/Test+Synthetic"),
            Encoding=pikepdf.Name("/Identity-H"),
            DescendantFonts=pikepdf.Array([pdf.make_indirect(descendant)]),
        )
        page["/Resources"] = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=pdf.make_indirect(font)))
        page["/Contents"] = pdf.make_indirect(pikepdf.Stream(pdf, b""))
        pdf.save(path)
        return path

    def _cidset_bytes(self, result_path: str) -> bytes:
        with pikepdf.open(result_path) as pdf:
            font = pdf.pages[0].obj["/Resources"]["/Font"]["/F1"]
            descriptor = font["/DescendantFonts"][0]["/FontDescriptor"]
            return bytes(descriptor["/CIDSet"].read_bytes())

    def test_repair_marks_every_glyph_present(self) -> None:
        # Only CIDs 0 and 1 marked, but the real font program has 5 glyphs (0-4) — matches the
        # real, confirmed defect: a CIDSet reflecting document usage, not the font program.
        input_path = self._write_pdf_with_cid_font(
            num_glyphs=5, cidset_bytes=_pack_cidset({0, 1}, num_bytes=1)
        )

        result = FontRepairPikePdfAdapter().repair(input_path, output_dir=self.output_dir)

        self.assertEqual(self._cidset_bytes(result), _pack_cidset({0, 1, 2, 3, 4}, num_bytes=1))

    def test_repair_regenerates_cidset_that_is_too_short_to_represent_all_glyphs(self) -> None:
        # The real, worse-case instance found: a CIDSet stream too short to even represent the
        # font's actual glyph count (12 bytes for a font with 4685 glyphs, needing 586).
        input_path = self._write_pdf_with_cid_font(
            num_glyphs=20, cidset_bytes=_pack_cidset({0}, num_bytes=1)
        )

        result = FontRepairPikePdfAdapter().repair(input_path, output_dir=self.output_dir)

        self.assertEqual(self._cidset_bytes(result), _pack_cidset(set(range(20)), num_bytes=3))

    def test_repair_leaves_already_correct_cidset_untouched(self) -> None:
        already_correct = _pack_cidset({0, 1, 2, 3, 4}, num_bytes=1)
        input_path = self._write_pdf_with_cid_font(num_glyphs=5, cidset_bytes=already_correct)

        result = FontRepairPikePdfAdapter().repair(input_path, output_dir=self.output_dir)

        self.assertEqual(self._cidset_bytes(result), already_correct)

    def test_repair_skips_font_with_no_cidset(self) -> None:
        # Rule 7.21.4.2 only applies when a CIDSet exists at all — no CIDSet is not a defect.
        input_path = self._write_pdf_with_cid_font(num_glyphs=5, cidset_bytes=None)

        result = FontRepairPikePdfAdapter().repair(input_path, output_dir=self.output_dir)

        with pikepdf.open(result) as pdf:
            font = pdf.pages[0].obj["/Resources"]["/Font"]["/F1"]
            descriptor = font["/DescendantFonts"][0]["/FontDescriptor"]
            self.assertNotIn("/CIDSet", descriptor)

    def test_repair_skips_font_without_fontfile2(self) -> None:
        # No embedded FontFile2 (e.g. a CFF/FontFile3 font) — out of scope, left untouched.
        incomplete = _pack_cidset({0}, num_bytes=1)
        input_path = self._write_pdf_with_cid_font(cidset_bytes=incomplete, no_fontfile=True)

        result = FontRepairPikePdfAdapter().repair(input_path, output_dir=self.output_dir)

        self.assertEqual(self._cidset_bytes(result), incomplete)

    def test_repair_skips_font_with_non_type0_subtype_when_scanning_for_cidsets(self) -> None:
        path = os.path.join(self.input_dir, "document.pdf")
        pdf = pikepdf.new()
        page = pdf.add_blank_page(page_size=(400, 200))
        simple_font = pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name("/Font"),
                Subtype=pikepdf.Name("/TrueType"),
                BaseFont=pikepdf.Name("/Helvetica"),
            )
        )
        page["/Resources"] = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=simple_font))
        page["/Contents"] = pdf.make_indirect(pikepdf.Stream(pdf, b""))
        pdf.save(path)

        result = FontRepairPikePdfAdapter().repair(path, output_dir=self.output_dir)

        self.assertTrue(os.path.exists(result))

    def test_repair_skips_type0_font_with_missing_descendant_fonts_when_scanning_for_cidsets(
        self,
    ) -> None:
        path = os.path.join(self.input_dir, "document.pdf")
        pdf = pikepdf.new()
        page = pdf.add_blank_page(page_size=(400, 200))
        font = pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name("/Font"),
                Subtype=pikepdf.Name("/Type0"),
                BaseFont=pikepdf.Name("/Test"),
                Encoding=pikepdf.Name("/Identity-H"),
            )
        )
        page["/Resources"] = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
        page["/Contents"] = pdf.make_indirect(pikepdf.Stream(pdf, b""))
        pdf.save(path)

        result = FontRepairPikePdfAdapter().repair(path, output_dir=self.output_dir)

        self.assertTrue(os.path.exists(result))

    def test_repair_skips_descendant_font_with_no_font_descriptor(self) -> None:
        path = os.path.join(self.input_dir, "document.pdf")
        pdf = pikepdf.new()
        page = pdf.add_blank_page(page_size=(400, 200))
        descendant = pikepdf.Dictionary(
            Type=pikepdf.Name("/Font"),
            Subtype=pikepdf.Name("/CIDFontType2"),
            BaseFont=pikepdf.Name("/Test"),
            CIDSystemInfo=pikepdf.Dictionary(Registry="Adobe", Ordering="Identity", Supplement=0),
            CIDToGIDMap=pikepdf.Name("/Identity"),
        )
        font = pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name("/Font"),
                Subtype=pikepdf.Name("/Type0"),
                BaseFont=pikepdf.Name("/Test"),
                Encoding=pikepdf.Name("/Identity-H"),
                DescendantFonts=pikepdf.Array([pdf.make_indirect(descendant)]),
            )
        )
        page["/Resources"] = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
        page["/Contents"] = pdf.make_indirect(pikepdf.Stream(pdf, b""))
        pdf.save(path)

        result = FontRepairPikePdfAdapter().repair(path, output_dir=self.output_dir)

        self.assertTrue(os.path.exists(result))

    def test_repair_leaves_cidset_untouched_for_unparseable_fontfile2(self) -> None:
        # A corrupt/garbage FontFile2 — fontTools can't read a glyph count from it, so the
        # repair must decline (never guess) rather than crash the whole document.
        incomplete = _pack_cidset({0}, num_bytes=1)
        path = os.path.join(self.input_dir, "document.pdf")
        pdf = pikepdf.new()
        page = pdf.add_blank_page(page_size=(400, 200))
        descriptor = pikepdf.Dictionary(
            Type=pikepdf.Name("/FontDescriptor"), FontName=pikepdf.Name("/Test")
        )
        descriptor["/FontFile2"] = pdf.make_indirect(
            pikepdf.Stream(pdf, b"not a real font file, just garbage bytes")
        )
        descriptor["/CIDSet"] = pdf.make_indirect(pikepdf.Stream(pdf, incomplete))
        descendant = pikepdf.Dictionary(
            Type=pikepdf.Name("/Font"),
            Subtype=pikepdf.Name("/CIDFontType2"),
            BaseFont=pikepdf.Name("/Test"),
            CIDSystemInfo=pikepdf.Dictionary(Registry="Adobe", Ordering="Identity", Supplement=0),
            CIDToGIDMap=pikepdf.Name("/Identity"),
            FontDescriptor=pdf.make_indirect(descriptor),
        )
        font = pikepdf.Dictionary(
            Type=pikepdf.Name("/Font"),
            Subtype=pikepdf.Name("/Type0"),
            BaseFont=pikepdf.Name("/Test"),
            Encoding=pikepdf.Name("/Identity-H"),
            DescendantFonts=pikepdf.Array([pdf.make_indirect(descendant)]),
        )
        page["/Resources"] = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=pdf.make_indirect(font)))
        page["/Contents"] = pdf.make_indirect(pikepdf.Stream(pdf, b""))
        pdf.save(path)

        result = FontRepairPikePdfAdapter().repair(path, output_dir=self.output_dir)

        self.assertEqual(self._cidset_bytes(result), incomplete)

    def test_repair_deduplicates_cidset_repair_for_descriptor_shared_across_pages(self) -> None:
        # Confirms `_repair_cidsets`'s documented dedup (`seen_descriptor_ids`) actually
        # produces a fully and correctly repaired CIDSet on both pages, not just that the
        # dedup bookkeeping exists in the code.
        path = os.path.join(self.input_dir, "document.pdf")
        pdf = pikepdf.new()
        font_data = _minimal_ttf_bytes(5)
        descriptor = pikepdf.Dictionary(
            Type=pikepdf.Name("/FontDescriptor"), FontName=pikepdf.Name("/Test")
        )
        descriptor["/FontFile2"] = pdf.make_indirect(pikepdf.Stream(pdf, font_data))
        descriptor["/CIDSet"] = pdf.make_indirect(
            pikepdf.Stream(pdf, _pack_cidset({0, 1}, num_bytes=1))
        )
        descendant = pikepdf.Dictionary(
            Type=pikepdf.Name("/Font"),
            Subtype=pikepdf.Name("/CIDFontType2"),
            BaseFont=pikepdf.Name("/Test"),
            CIDSystemInfo=pikepdf.Dictionary(Registry="Adobe", Ordering="Identity", Supplement=0),
            CIDToGIDMap=pikepdf.Name("/Identity"),
            FontDescriptor=pdf.make_indirect(descriptor),
        )
        shared_font = pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name("/Font"),
                Subtype=pikepdf.Name("/Type0"),
                BaseFont=pikepdf.Name("/Test"),
                Encoding=pikepdf.Name("/Identity-H"),
                DescendantFonts=pikepdf.Array([pdf.make_indirect(descendant)]),
            )
        )

        page1 = pdf.add_blank_page(page_size=(400, 200))
        page1["/Resources"] = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=shared_font))
        page1["/Contents"] = pdf.make_indirect(pikepdf.Stream(pdf, b""))
        page2 = pdf.add_blank_page(page_size=(400, 200))
        page2["/Resources"] = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=shared_font))
        page2["/Contents"] = pdf.make_indirect(pikepdf.Stream(pdf, b""))
        pdf.save(path)

        result = FontRepairPikePdfAdapter().repair(path, output_dir=self.output_dir)

        expected = _pack_cidset({0, 1, 2, 3, 4}, num_bytes=1)
        with pikepdf.open(result) as out_pdf:
            for page in out_pdf.pages:
                font = page.obj["/Resources"]["/Font"]["/F1"]
                descriptor_out = font["/DescendantFonts"][0]["/FontDescriptor"]
                self.assertEqual(bytes(descriptor_out["/CIDSet"].read_bytes()), expected)


def _png_bytes(size: tuple[int, int], color: tuple[int, int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="PNG")
    return buf.getvalue()


class AltTextAdapterTests(SimpleTestCase):
    def setUp(self) -> None:
        self.input_dir = tempfile.mkdtemp()
        self.output_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.input_dir, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.output_dir, ignore_errors=True)

    def _write_tagged_pdf(
        self,
        name: str = "document.pdf",
        *,
        image_sizes: tuple[tuple[int, int], ...] = (),
        extra_figures_without_image: int = 0,
        alt_indices: dict[int, str] | None = None,
        tagged: bool = True,
    ) -> str:
        """Builds a one-page PDF with real embedded images (distinct colors, so PyMuPDF
        doesn't dedup identical XObjects), then — unless `tagged=False` — a struct tree
        with one `<Figure>` element per image plus `extra_figures_without_image` more
        (which won't ordinally pair to any image, exercising the full-page-render
        fallback). `alt_indices` pre-populates `/Alt` on specific figures by index.
        """
        path = os.path.join(self.input_dir, name)
        colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]

        doc = fitz.open()
        page = doc.new_page(width=300, height=300)
        for i, (w, h) in enumerate(image_sizes):
            page.insert_image(
                fitz.Rect(10, 10, 110, 110), stream=_png_bytes((w, h), colors[i % len(colors)])
            )
        doc.save(path)
        doc.close()

        if not tagged:
            return path

        alt_indices = alt_indices or {}
        figure_count = len(image_sizes) + extra_figures_without_image
        with pikepdf.open(path, allow_overwriting_input=True) as pdf:
            page_obj = pdf.pages[0].obj
            elements = []
            for fig_n in range(figure_count):
                elem = pikepdf.Dictionary(S=pikepdf.Name("/Figure"), Pg=page_obj)
                if fig_n in alt_indices:
                    elem["/Alt"] = pikepdf.String(alt_indices[fig_n])
                elements.append(pdf.make_indirect(elem))
            pdf.Root.StructTreeRoot = pdf.make_indirect(
                pikepdf.Dictionary(Type=pikepdf.Name("/StructTreeRoot"), K=pikepdf.Array(elements))
            )
            pdf.save(path)

        return path

    def test_collect_figures_returns_candidate_with_extracted_image(self) -> None:
        path = self._write_tagged_pdf(image_sizes=[(200, 200)])

        candidates = AltTextPikePdfAdapter().collect_figures(path)

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate.ref, (0, 0))
        self.assertEqual(candidate.page_number, 1)
        self.assertFalse(candidate.decorative)
        self.assertTrue(candidate.image_bytes)
        self.assertIn(candidate.media_type, ("image/png", "image/jpeg"))

    def test_collect_figures_skips_figures_that_already_have_alt(self) -> None:
        path = self._write_tagged_pdf(
            image_sizes=[(200, 200), (200, 200)], alt_indices={0: "already described"}
        )

        candidates = AltTextPikePdfAdapter().collect_figures(path)

        # Only one candidate — the other Figure was filtered out before enumeration, same
        # as v1, so the surviving one is fig_n 0 within the *filtered* list, not its
        # original struct-tree position.
        self.assertEqual([c.ref for c in candidates], [(0, 0)])

    def test_collect_figures_treats_opendataloader_placeholder_alt_as_missing(self) -> None:
        # OpenDataLoader stamps "image " + counter on every Figure by default (see the
        # module docstring's `_PLACEHOLDER_ALT_PATTERN` comment) — this must NOT be
        # mistaken for a real description, or every real document would get skipped.
        path = self._write_tagged_pdf(
            image_sizes=[(200, 200), (200, 200)],
            alt_indices={0: "image 1", 1: "a real hand-written description"},
        )

        candidates = AltTextPikePdfAdapter().collect_figures(path)

        self.assertEqual([c.ref for c in candidates], [(0, 0)])

    def test_collect_figures_returns_empty_list_when_no_struct_tree_root(self) -> None:
        path = self._write_tagged_pdf(image_sizes=[(200, 200)], tagged=False)

        candidates = AltTextPikePdfAdapter().collect_figures(path)

        self.assertEqual(candidates, [])

    def test_collect_figures_flags_small_image_as_decorative(self) -> None:
        path = self._write_tagged_pdf(image_sizes=[(10, 10)])

        candidates = AltTextPikePdfAdapter().collect_figures(path)

        self.assertEqual(len(candidates), 1)
        self.assertTrue(candidates[0].decorative)

    def test_collect_figures_falls_back_to_full_page_render_when_no_matching_image(self) -> None:
        path = self._write_tagged_pdf(image_sizes=[(200, 200)], extra_figures_without_image=1)

        candidates = AltTextPikePdfAdapter().collect_figures(path)

        self.assertEqual(len(candidates), 2)
        fallback = candidates[1]
        self.assertEqual(fallback.ref, (0, 1))
        self.assertFalse(fallback.decorative)
        self.assertEqual(fallback.media_type, "image/png")
        self.assertTrue(fallback.image_bytes)

    def test_write_alt_text_writes_output_under_input_basename(self) -> None:
        path = self._write_tagged_pdf(image_sizes=[(200, 200)])

        result = AltTextPikePdfAdapter().write_alt_text(
            path, output_dir=self.output_dir, alt_by_ref={(0, 0): "a red square"}
        )

        self.assertEqual(result, os.path.join(self.output_dir, "document.pdf"))

    def test_write_alt_text_sets_alt_only_for_non_empty_values(self) -> None:
        path = self._write_tagged_pdf(image_sizes=[(200, 200), (200, 200)])

        result = AltTextPikePdfAdapter().write_alt_text(
            path,
            output_dir=self.output_dir,
            alt_by_ref={(0, 0): "a red square", (0, 1): ""},
        )

        with pikepdf.open(result) as pdf:
            figures = list(pdf.Root.StructTreeRoot.K)
            self.assertEqual(str(figures[0].Alt), "a red square")
            self.assertNotIn("/Alt", figures[1])

    def test_collect_figures_raises_adapter_error_for_unopenable_pdf(self) -> None:
        bad_path = os.path.join(self.input_dir, "not-a-pdf.pdf")
        with open(bad_path, "w") as f:
            f.write("not a pdf")

        with self.assertRaises(AdapterError):
            AltTextPikePdfAdapter().collect_figures(bad_path)

    def test_write_alt_text_raises_adapter_error_for_unopenable_pdf(self) -> None:
        bad_path = os.path.join(self.input_dir, "not-a-pdf.pdf")
        with open(bad_path, "w") as f:
            f.write("not a pdf")

        with self.assertRaises(AdapterError):
            AltTextPikePdfAdapter().write_alt_text(
                bad_path, output_dir=self.output_dir, alt_by_ref={}
            )


class ClaudeVisionClientTests(SimpleTestCase):
    # Not autospec'd: anthropic.Anthropic exposes `.messages` as a `cached_property`, which
    # autospec can only introspect as an opaque property (not the `Messages` resource type
    # it actually returns), so an autospec'd mock has no `.messages.create` to configure.
    @patch("remediation.adapters.alt_text.claude_vision.anthropic.Anthropic")
    def test_describe_returns_stripped_text_from_response(self, mock_anthropic_cls) -> None:
        mock_anthropic_cls.return_value.messages.create.return_value = SimpleNamespace(
            content=[SimpleNamespace(text="  A red square.  ")]
        )

        result = ClaudeVisionClient(api_key="test-key").describe(
            b"fake-bytes", media_type="image/png", document_title="Test Doc", page_number=3
        )

        self.assertEqual(result, "A red square.")

    @patch("remediation.adapters.alt_text.claude_vision.anthropic.Anthropic")
    def test_describe_sends_image_and_context_in_prompt(self, mock_anthropic_cls) -> None:
        mock_client = mock_anthropic_cls.return_value
        mock_client.messages.create.return_value = SimpleNamespace(
            content=[SimpleNamespace(text="ok")]
        )

        ClaudeVisionClient(api_key="test-key", model="claude-sonnet-5").describe(
            b"fake-bytes", media_type="image/png", document_title="Test Doc", page_number=3
        )

        _, kwargs = mock_client.messages.create.call_args
        self.assertEqual(kwargs["model"], "claude-sonnet-5")
        content = kwargs["messages"][0]["content"]
        self.assertEqual(content[0]["source"]["media_type"], "image/png")
        self.assertIn("Test Doc", content[1]["text"])
        self.assertIn("page 3", content[1]["text"])

    def test_describe_raises_adapter_error_when_api_key_missing(self) -> None:
        with self.assertRaises(AdapterError):
            ClaudeVisionClient(api_key="").describe(
                b"fake-bytes", media_type="image/png", document_title="", page_number=1
            )

    @patch("remediation.adapters.alt_text.claude_vision.anthropic.Anthropic")
    def test_describe_raises_adapter_error_on_api_failure(self, mock_anthropic_cls) -> None:
        mock_anthropic_cls.return_value.messages.create.side_effect = Exception("rate limited")

        with self.assertRaises(AdapterError):
            ClaudeVisionClient(api_key="test-key").describe(
                b"fake-bytes", media_type="image/png", document_title="", page_number=1
            )

    def test_uses_default_model_when_not_specified(self) -> None:
        client = ClaudeVisionClient(api_key="test-key")

        self.assertEqual(client.model, "claude-sonnet-5")


class ScoringPikePdfAdapterTests(SimpleTestCase):
    def setUp(self) -> None:
        self.input_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.input_dir, ignore_errors=True)

    def _write_blank_untagged_pdf(self, name: str = "blank.pdf") -> str:
        path = os.path.join(self.input_dir, name)
        pdf = pikepdf.new()
        pdf.add_blank_page(page_size=(200, 200))
        pdf.save(path)
        return path

    def _write_tagged_pdf_with_text(self, name: str = "tagged.pdf") -> str:
        path = os.path.join(self.input_dir, name)
        doc = fitz.open()
        page = doc.new_page(width=300, height=300)
        page.insert_text((50, 50), "Hello world " * 10)
        doc.save(path)
        doc.close()
        with pikepdf.open(path, allow_overwriting_input=True) as pdf:
            pdf.Root.StructTreeRoot = pdf.make_indirect(
                pikepdf.Dictionary(Type=pikepdf.Name("/StructTreeRoot"), K=pikepdf.Array())
            )
            pdf.save(path)
        return path

    def test_score_deducts_structure_text_and_contrast_for_blank_pdf(self) -> None:
        path = self._write_blank_untagged_pdf()

        result = ScoringPikePdfAdapter().score(path)

        self.assertEqual(result.score, 57)
        self.assertEqual(result.grade, "D")
        self.assertEqual(len(result.manual_review_items), 3)

    def test_score_only_deducts_contrast_when_structure_and_text_present(self) -> None:
        path = self._write_tagged_pdf_with_text()

        result = ScoringPikePdfAdapter().score(path)

        self.assertEqual(result.score, 95)
        self.assertEqual(result.grade, "A")
        self.assertEqual(len(result.manual_review_items), 1)

    def test_score_degrades_gracefully_instead_of_raising_for_unopenable_pdf(self) -> None:
        # Per-check try/except in _assess (ported from assess_document) swallows a
        # corrupt file into an all-defaults issues dict rather than raising — same
        # score as the blank-PDF case, not an AdapterError.
        bad_path = os.path.join(self.input_dir, "not-a-pdf.pdf")
        with open(bad_path, "w") as f:
            f.write("not a pdf")

        result = ScoringPikePdfAdapter().score(bad_path)

        self.assertEqual(result.score, 57)
        self.assertEqual(result.grade, "D")
