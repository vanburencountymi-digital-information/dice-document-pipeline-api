from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from remediation.adapters.base import AdapterError
from remediation.tests.helpers import make_tagged_pdf

COMMAND = "remediation.management.commands.pdf_quality_report"
PAGES = [{"text": "the board approved the budget amendment", "tags": [("H1",), ("L", 2)]}]


def failed_rule(clause: str, test_number: str, failed_checks: int) -> MagicMock:
    return MagicMock(clause=clause, test_number=test_number, failed_checks=failed_checks)


class PdfQualityReportTests(SimpleTestCase):
    def setUp(self) -> None:
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)
        self.pdf = make_tagged_pdf(self.dir / "doc.pdf", PAGES)
        self.output_dir = self.dir / "reports"

    def run_report(self, *args: object) -> dict:
        call_command(
            "pdf_quality_report", *args, "--output-dir", str(self.output_dir), stdout=StringIO()
        )
        return json.loads((self.output_dir / "doc.json").read_text())

    def test_reports_on_an_existing_output_without_veraPDF(self) -> None:
        result = self.run_report(self.pdf, "--no-verapdf")

        self.assertEqual(result["file"], "doc.pdf")
        self.assertEqual(result["totals"]["headings"], 1)
        self.assertEqual(result["totals"]["list_items"], 2)
        self.assertIsNone(result["verapdf"])

    @patch(f"{COMMAND}.VeraPDFAdapter", autospec=True)
    def test_adds_veraPDF_failed_checks_by_rule(self, adapter_class: MagicMock) -> None:
        adapter_class.return_value.validate.return_value.failed_rules = [
            failed_rule("7.1", "3", 11),
            failed_rule("6.2", "1", 1),
        ]

        result = self.run_report(self.pdf)

        self.assertEqual(result["verapdf"], {"7.1/3": 11, "6.2/1": 1})

    def test_measures_text_against_the_original_when_given_a_source_dir(self) -> None:
        source_dir = self.dir / "originals"
        source_dir.mkdir()
        make_tagged_pdf(source_dir / "doc.pdf", [{"text": "the board approved the budget"}])

        result = self.run_report(self.pdf, "--no-verapdf", "--source-dir", str(source_dir))

        self.assertEqual(result["totals"]["words_missing_from_source"], 0)
        self.assertEqual(result["totals"]["words_added_to_source"], 1)

    def test_raises_when_the_original_is_missing_from_the_source_dir(self) -> None:
        (self.dir / "empty").mkdir()
        with self.assertRaisesMessage(CommandError, "No original named doc.pdf"):
            self.run_report(self.pdf, "--no-verapdf", "--source-dir", str(self.dir / "empty"))

    def test_raises_for_a_file_that_does_not_exist(self) -> None:
        with self.assertRaisesMessage(CommandError, "Not a file"):
            self.run_report(self.dir / "nope.pdf")

    @patch(f"{COMMAND}._run_stages", autospec=True)
    def test_run_pipeline_reports_on_the_final_pdf_and_records_the_stages(
        self, run_stages: MagicMock
    ) -> None:
        final = make_tagged_pdf(self.dir / "final.pdf", PAGES)
        stages = {"ocr": {"seconds": 1.5, "verapdf": {"7.1/3": 4}}}
        run_stages.return_value = (final, stages, {"7.1/3": 4})

        result = self.run_report(self.pdf, "--run-pipeline")

        self.assertEqual(result["file"], "doc.pdf")
        self.assertEqual(result["stages"], stages)
        self.assertEqual(result["verapdf"], {"7.1/3": 4})
        self.assertEqual(result["totals"]["headings"], 1)

    @patch(f"{COMMAND}._run_stages", autospec=True)
    def test_run_pipeline_raises_when_every_step_failed(self, run_stages: MagicMock) -> None:
        run_stages.return_value = (self.pdf, {"ocr": {"error": "boom"}}, None)
        with self.assertRaisesMessage(CommandError, "Every pipeline step failed"):
            self.run_report(self.pdf, "--run-pipeline")


class RunStagesTests(SimpleTestCase):
    """`_run_stages` itself: a step that raises must not stop the ones after it."""

    @patch(f"{COMMAND}.LinkAdapter", autospec=True)
    @patch(f"{COMMAND}.MetadataAdapter", autospec=True)
    @patch(f"{COMMAND}.FontRepairAdapter", autospec=True)
    @patch(f"{COMMAND}.OpenDataLoaderAdapter", autospec=True)
    def test_a_failed_step_is_recorded_and_the_next_step_gets_the_last_good_pdf(
        self, ocr: MagicMock, font: MagicMock, metadata: MagicMock, link: MagicMock
    ) -> None:
        from remediation.management.commands.pdf_quality_report import _run_stages

        ocr.return_value.extract.return_value = "/work/ocr/doc.pdf"
        font.return_value.repair.side_effect = AdapterError("font step broke")
        metadata.return_value.finalize.return_value = "/work/metadata/doc.pdf"
        link.return_value.repair.return_value = "/work/link_tag/doc.pdf"

        final, stages, verapdf = _run_stages(Path("/in/doc.pdf"), Path("/work"), use_verapdf=False)

        self.assertEqual(final, Path("/work/link_tag/doc.pdf"))
        self.assertEqual(stages["font_repair"], {"error": "font step broke"})
        self.assertEqual(metadata.return_value.finalize.call_args.args[0], "/work/ocr/doc.pdf")
        self.assertIsNone(verapdf)
