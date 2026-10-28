from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from django.test import SimpleTestCase
from parameterized import parameterized

from remediation.pdf_quality import analyze_pdf, compare_reports
from remediation.tests.helpers import make_tagged_pdf

SENTENCE = "the board approved the budget amendment as presented"


class AnalyzePdfTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        temp = TemporaryDirectory()
        cls.addClassCleanup(temp.cleanup)
        cls.dir = Path(temp.name)

    def analyze(self, pages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        path = make_tagged_pdf(self.dir / "out.pdf", pages)
        return analyze_pdf(path, **kwargs)

    def test_counts_headings_per_page_and_total(self) -> None:
        report = self.analyze(
            [{"text": SENTENCE, "tags": [("H1",), ("H2",)]}, {"text": SENTENCE, "tags": [("P",)]}]
        )
        self.assertEqual(report["pages"]["1"]["headings"], [1, 2])
        self.assertEqual(report["pages"]["2"]["headings"], [])
        self.assertEqual(report["totals"]["headings"], 2)

    @parameterized.expand(
        [
            ("h1_to_h3_is_a_skip", [("H1",), ("H3",)], 1),
            ("h1_to_h2_is_fine", [("H1",), ("H2",)], 0),
            ("going_back_up_is_fine", [("H1",), ("H2",), ("H1",)], 0),
        ]
    )
    def test_counts_heading_level_skips(self, _name: str, tags: list[Any], expected: int) -> None:
        report = self.analyze([{"text": SENTENCE, "tags": tags}])
        self.assertEqual(report["totals"]["heading_level_skips"], expected)

    def test_records_how_many_items_are_in_each_list(self) -> None:
        report = self.analyze([{"text": SENTENCE, "tags": [("L", 2), ("L", 3)]}])
        self.assertEqual(report["pages"]["1"]["lists"], [2, 3])
        self.assertEqual(report["totals"]["lists"], 2)
        self.assertEqual(report["totals"]["list_items"], 5)

    @parameterized.expand(
        [
            ("with_header_cells", 2, 0),
            ("without_header_cells", 0, 1),
        ]
    )
    def test_flags_tables_without_header_cells(
        self, _name: str, header_cells: int, expected_without: int
    ) -> None:
        report = self.analyze([{"text": SENTENCE, "tags": [("Table", header_cells)]}])
        self.assertEqual(report["totals"]["tables"], 1)
        self.assertEqual(report["totals"]["tables_without_headers"], expected_without)

    @parameterized.expand(
        [
            ("real_alt", "A county road crew repairing a bridge", 0, 0),
            ("placeholder_alt", "image 6", 0, 1),
            ("bare_number_alt", "12", 0, 1),
            ("no_alt", "", 1, 0),
        ]
    )
    def test_classifies_figure_alt_text(
        self, _name: str, alt: str, expected_missing: int, expected_placeholder: int
    ) -> None:
        report = self.analyze([{"text": SENTENCE, "tags": [("Figure", alt)]}])
        self.assertEqual(report["totals"]["figures"], 1)
        self.assertEqual(report["totals"]["figures_without_alt"], expected_missing)
        self.assertEqual(report["totals"]["figures_with_placeholder_alt"], expected_placeholder)

    def test_counts_pages_with_almost_no_text_as_empty(self) -> None:
        report = self.analyze([{"text": SENTENCE}, {"text": ""}])
        self.assertEqual(report["totals"]["empty_pages"], 1)

    def test_untagged_pdf_reports_no_structure(self) -> None:
        report = self.analyze([{"text": SENTENCE}])
        self.assertEqual(report["tag_counts"], {})
        self.assertEqual(report["totals"]["headings"], 0)

    def test_same_report_every_time(self) -> None:
        pages = [{"text": SENTENCE, "tags": [("H1",), ("L", 2)]}]
        self.assertEqual(self.analyze(pages), self.analyze(pages))

    def test_with_source_counts_text_laid_down_twice(self) -> None:
        source = make_tagged_pdf(self.dir / "source.pdf", [{"text": SENTENCE}])
        report = self.analyze([{"text": f"{SENTENCE} {SENTENCE}"}], source_path=source)
        self.assertGreater(report["totals"]["repeated_phrases"], 0)
        self.assertEqual(report["totals"]["words_missing_from_source"], 0)

    def test_with_source_counts_lost_words(self) -> None:
        source = make_tagged_pdf(self.dir / "source.pdf", [{"text": SENTENCE}])
        report = self.analyze([{"text": "the board approved"}], source_path=source)
        self.assertEqual(report["totals"]["words_missing_from_source"], 5)
        self.assertEqual(report["totals"]["repeated_phrases"], 0)

    def test_reordered_text_is_not_counted_as_repeated(self) -> None:
        source = make_tagged_pdf(self.dir / "source.pdf", [{"text": SENTENCE}])
        report = self.analyze([{"text": "as presented the budget amendment"}], source_path=source)
        self.assertEqual(report["totals"]["repeated_phrases"], 0)

    def test_with_source_of_a_different_length_raises(self) -> None:
        source = make_tagged_pdf(self.dir / "source.pdf", [{"text": SENTENCE}])
        with self.assertRaises(ValueError):
            self.analyze([{"text": SENTENCE}, {"text": SENTENCE}], source_path=source)


def report(**totals: Any) -> dict[str, Any]:
    """A minimal report in the shape `analyze_pdf` returns, for comparing without PDFs."""
    return {
        "document": {"title": "doc", "language": "en-us"},
        "totals": {"headings": 5, "empty_pages": 0, "lists": 2, **totals},
        "pages": {"1": page(), "2": page()},
        "verapdf": None,
    }


def page(**changes: Any) -> dict[str, Any]:
    return {
        "words": 100,
        "headings": [1],
        "lists": [3],
        "tables": 0,
        "figures": 0,
        "tag_signature": "abc",
        **changes,
    }


class CompareReportsTests(SimpleTestCase):
    @parameterized.expand(
        [
            ("fewer_headings", {"headings": 3}),
            ("more_blank_pages", {"empty_pages": 1}),
            ("more_placeholder_alt", {"figures_with_placeholder_alt": 2}),
        ]
    )
    def test_worse_totals_are_regressions(self, _name: str, changes: dict[str, int]) -> None:
        baseline = report(figures_with_placeholder_alt=0)
        candidate = report(**{"figures_with_placeholder_alt": 0, **changes})
        self.assertTrue(compare_reports(baseline, candidate).regressions)

    @parameterized.expand(
        [
            ("more_headings", {"headings": 7}),
            ("fewer_blank_pages", {"empty_pages": 0}),
        ]
    )
    def test_better_totals_are_improvements(self, _name: str, changes: dict[str, int]) -> None:
        baseline = report(empty_pages=1)
        candidate = report(**{"empty_pages": 1, **changes})
        comparison = compare_reports(baseline, candidate)
        self.assertEqual(comparison.regressions, [])
        self.assertTrue(comparison.improvements)

    def test_a_change_in_list_count_alone_is_not_a_regression(self) -> None:
        comparison = compare_reports(report(lists=2), report(lists=4))
        self.assertEqual(comparison.regressions, [])
        self.assertIn(("lists", 2, 4), comparison.rows)

    def test_identical_reports_have_no_differences(self) -> None:
        comparison = compare_reports(report(), report())
        self.assertEqual(comparison.regressions, [])
        self.assertEqual(comparison.improvements, [])
        self.assertEqual(comparison.page_changes, [])

    def test_names_the_page_where_a_list_was_split(self) -> None:
        candidate = report()
        candidate["pages"]["2"] = page(lists=[1, 1, 1])
        comparison = compare_reports(report(), candidate)
        self.assertEqual(comparison.page_changes, ["page 2: list sizes [3] -> [1, 1, 1]"])

    def test_names_the_page_where_only_tag_order_changed(self) -> None:
        candidate = report()
        candidate["pages"]["1"] = page(tag_signature="xyz")
        comparison = compare_reports(report(), candidate)
        self.assertEqual(comparison.page_changes, ["page 1: tag order changed"])

    def test_a_veraPDF_rule_with_more_failures_is_a_regression(self) -> None:
        baseline, candidate = report(), report()
        baseline["verapdf"], candidate["verapdf"] = {"7.1/3": 10}, {"7.1/3": 12, "7.2/1": 1}
        comparison = compare_reports(baseline, candidate)
        self.assertEqual(
            comparison.regressions, ["veraPDF 7.1/3: 10 -> 12", "veraPDF 7.2/1: 0 -> 1"]
        )

    def test_a_changed_document_property_is_a_regression(self) -> None:
        candidate = report()
        candidate["document"]["language"] = ""
        comparison = compare_reports(report(), candidate)
        self.assertEqual(comparison.regressions, ["document language: 'en-us' -> ''"])
