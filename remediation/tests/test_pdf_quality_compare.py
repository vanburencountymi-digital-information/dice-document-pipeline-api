from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from remediation.tests.test_pdf_quality import report


class PdfQualityCompareTests(SimpleTestCase):
    def setUp(self) -> None:
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)

    def write(self, folder: str, name: str, content: dict[str, Any]) -> Path:
        (self.dir / folder).mkdir(exist_ok=True)
        path = self.dir / folder / name
        path.write_text(json.dumps(content))
        return path

    def run_compare(self, *args: Any) -> str:
        out = StringIO()
        call_command("pdf_quality_compare", *args, stdout=out)
        return out.getvalue()

    def test_prints_what_got_worse_and_the_pages_that_changed(self) -> None:
        worse = report(headings=3)
        worse["pages"]["1"]["headings"] = []
        base = self.write("a", "doc.json", report())
        cand = self.write("b", "doc.json", worse)

        output = self.run_compare(base, cand)

        self.assertIn("headings: 5 -> 3", output)
        self.assertIn("page 1: headings 1 -> 0", output)

    def test_does_not_fail_on_a_regression_by_default(self) -> None:
        base = self.write("a", "doc.json", report())
        cand = self.write("b", "doc.json", report(headings=3))
        self.run_compare(base, cand)

    def test_fail_on_regression_raises_when_something_got_worse(self) -> None:
        base = self.write("a", "doc.json", report())
        cand = self.write("b", "doc.json", report(headings=3))
        with self.assertRaisesMessage(CommandError, "Regressions in: report"):
            self.run_compare(base, cand, "--fail-on-regression")

    def test_fail_on_regression_passes_when_nothing_got_worse(self) -> None:
        base = self.write("a", "doc.json", report())
        cand = self.write("b", "doc.json", report(headings=8))
        self.run_compare(base, cand, "--fail-on-regression")

    def test_compares_two_folders_by_file_name_and_warns_about_the_rest(self) -> None:
        self.write("a", "one.json", report())
        self.write("a", "only_a.json", report())
        self.write("b", "one.json", report(headings=3))

        output = self.run_compare(self.dir / "a", self.dir / "b")

        self.assertIn("=== one.json ===", output)
        self.assertIn("Only on one side, skipped: only_a.json", output)

    def test_raises_when_the_two_sides_share_no_report_names(self) -> None:
        self.write("a", "one.json", report())
        self.write("b", "two.json", report())
        with self.assertRaisesMessage(CommandError, "No report names in common"):
            self.run_compare(self.dir / "a", self.dir / "b")

    def test_raises_for_a_path_that_does_not_exist(self) -> None:
        with self.assertRaisesMessage(CommandError, "Not a file or folder"):
            self.run_compare(self.dir / "missing", self.dir / "missing")

    def test_a_report_with_a_failed_step_is_not_comparable_and_fails_the_check(self) -> None:
        failed = report(headings=0)
        failed["failed_steps"] = ["ocr"]
        base = self.write("a", "doc.json", report())
        cand = self.write("b", "doc.json", failed)

        output = self.run_compare(base, cand)
        self.assertIn("NOT COMPARABLE", output)
        self.assertIn("candidate: pipeline step failed: ocr", output)
        self.assertNotIn("headings", output)
        with self.assertRaisesMessage(CommandError, "Regressions in: report"):
            self.run_compare(base, cand, "--fail-on-regression")
