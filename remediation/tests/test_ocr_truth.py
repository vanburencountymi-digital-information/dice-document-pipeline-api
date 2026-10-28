from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pymupdf as fitz
from django.test import SimpleTestCase
from parameterized import parameterized

from remediation.ocr_truth import (
    compare_scores,
    edit_distance,
    error_rate,
    load_truth,
    normalize_words,
    numbers_in,
    reading_order_score,
    score_page,
    score_pdf,
)
from remediation.tests.helpers import make_tagged_pdf

FIXTURES = Path(__file__).resolve().parent / "fixtures"
TRUTH_FILES = sorted((FIXTURES / "ocr_truth").glob("*.json"))


class WordMetricsTests(SimpleTestCase):
    def test_normalize_words_treats_curly_quotes_and_dashes_as_plain(self) -> None:
        self.assertEqual(
            normalize_words("Clerk’s “Office” – Erin"), ["Clerk's", '"Office"', "-", "Erin"]
        )

    @parameterized.expand(
        [
            ("same", ["a", "b"], ["a", "b"], 0),
            ("one_swapped", ["a", "b", "c"], ["a", "x", "c"], 1),
            ("one_dropped", ["a", "b", "c"], ["a", "c"], 1),
            ("one_added", ["a", "c"], ["a", "b", "c"], 1),
            ("all_different", ["a", "b"], ["c", "d"], 2),
            ("empty_to_words", [], ["a", "b"], 2),
        ]
    )
    def test_edit_distance(self, _name: str, a: list[str], b: list[str], expected: int) -> None:
        self.assertEqual(edit_distance(a, b), expected)

    @parameterized.expand(
        [
            ("identical", ["a", "b", "c", "d"], ["a", "b", "c", "d"], 0.0),
            ("one_in_four_wrong", ["a", "b", "c", "d"], ["a", "x", "c", "d"], 0.25),
            ("nothing_read", ["a", "b"], [], 1.0),
            ("nothing_expected_nothing_read", [], [], 0.0),
            ("nothing_expected_but_text_read", [], ["a"], 1.0),
        ]
    )
    def test_error_rate(
        self, _name: str, truth: list[str], output: list[str], expected: float
    ) -> None:
        self.assertEqual(error_rate(truth, output), expected)

    def test_numbers_in_ignores_punctuation_round_the_edges_only(self) -> None:
        found = numbers_in(["$1,427.", "(2025)", "Firearm", "49032-9622", "$1,427"])
        self.assertEqual(found, {"$1,427": 2, "2025": 1, "49032-9622": 1})


class ReadingOrderTests(SimpleTestCase):
    @parameterized.expand(
        [
            ("same_order", ["a", "b", "c", "d"], ["a", "b", "c", "d"], 1.0),
            ("a_missing_word_is_not_an_order_problem", ["a", "b", "c", "d"], ["a", "c", "d"], 1.0),
            ("an_extra_word_is_not_an_order_problem", ["a", "b", "c"], ["a", "x", "b", "c"], 1.0),
            ("nothing_in_common", ["a", "b"], ["c", "d"], 0.0),
            ("both_empty", [], [], 1.0),
        ]
    )
    def test_score(self, _name: str, truth: list[str], output: list[str], expected: float) -> None:
        self.assertEqual(reading_order_score(truth, output), expected)

    def test_swapped_blocks_score_lower_than_one(self) -> None:
        truth = ["one", "two", "three", "four", "five", "six"]
        output = ["four", "five", "six", "one", "two", "three"]
        self.assertLess(reading_order_score(truth, output), 0.7)


class ScorePageTests(SimpleTestCase):
    def test_same_word_count_but_different_words_is_counted(self) -> None:
        score = score_page("the board approved the budget", "the bored approved the budget")

        self.assertEqual((score["truth_words"], score["output_words"]), (5, 5))
        self.assertEqual((score["words_missing"], score["words_extra"]), (1, 1))
        self.assertEqual(score["word_error_rate"], 0.2)
        self.assertEqual(score["example_missing"], ["board"])
        self.assertEqual(score["example_extra"], ["bored"])

    def test_a_wrong_digit_is_a_missing_and_an_extra_number(self) -> None:
        score = score_page("Total $1,427 in 2025", "Total $1,472 in 2025")

        self.assertEqual((score["numbers_missing"], score["numbers_extra"]), (1, 1))

    def test_numbers_repeated_in_the_output_are_extra(self) -> None:
        score = score_page("Firearm 7 7 4", "Firearm 7 7 4 7 7 4")

        self.assertEqual((score["numbers_missing"], score["numbers_extra"]), (0, 3))
        self.assertEqual(score["words_extra"], 3)

    def test_text_that_matches_exactly_scores_clean(self) -> None:
        score = score_page("Dear Chairman,\nThank you.", "Dear  Chairman, Thank you.")

        self.assertEqual(
            (score["word_error_rate"], score["words_missing"], score["reading_order"]),
            (0.0, 0, 1.0),
        )


class ScorePdfTests(SimpleTestCase):
    def setUp(self) -> None:
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)

    def truth_file(self, pages: dict[str, str]) -> Path:
        path = self.dir / "truth.json"
        path.write_text(json.dumps({"provenance": "test", "pages": pages}))
        return path

    def test_scores_each_page_that_has_a_transcription(self) -> None:
        pdf = make_tagged_pdf(
            self.dir / "doc.pdf",
            [{"text": "first page words"}, {"text": "second page words"}, {"text": "third"}],
        )

        result = score_pdf(
            pdf, self.truth_file({"1": "first page words", "2": "second page wrong"})
        )

        self.assertEqual(sorted(result["pages"]), ["1", "2"])
        self.assertEqual(result["pages"]["1"]["word_error_rate"], 0.0)
        self.assertGreater(result["pages"]["2"]["word_error_rate"], 0)
        self.assertEqual(result["provenance"], "test")

    def test_raises_for_a_page_the_pdf_does_not_have(self) -> None:
        pdf = make_tagged_pdf(self.dir / "doc.pdf", [{"text": "only page"}])
        with self.assertRaisesMessage(ValueError, "has page 2"):
            score_pdf(pdf, self.truth_file({"2": "missing page"}))

    def test_raises_for_a_truth_file_with_no_pages(self) -> None:
        pdf = make_tagged_pdf(self.dir / "doc.pdf", [{"text": "only page"}])
        path = self.dir / "bad.json"
        path.write_text("{}")
        with self.assertRaisesMessage(ValueError, "no 'pages'"):
            score_pdf(pdf, path)


def scores(**page: float) -> dict[str, dict[str, float]]:
    base = {
        "word_error_rate": 0.1,
        "char_error_rate": 0.1,
        "words_missing": 3,
        "words_extra": 3,
        "numbers_missing": 0,
        "numbers_extra": 0,
        "reading_order": 1.0,
    }
    return {"pages": {"1": {**base, **page}}}


class CompareScoresTests(SimpleTestCase):
    @parameterized.expand(
        [
            ("error_rate_up", {"word_error_rate": 0.3}, "OCR page 1 word error rate: 0.1 -> 0.3"),
            ("numbers_extra_up", {"numbers_extra": 4}, "OCR page 1 numbers extra: 0 -> 4"),
            ("numbers_missing_up", {"numbers_missing": 1}, "OCR page 1 numbers missing: 0 -> 1"),
            ("words_missing_up", {"words_missing": 9}, "OCR page 1 words missing: 3 -> 9"),
            ("order_down", {"reading_order": 0.8}, "OCR page 1 reading order: 1.0 -> 0.8"),
        ]
    )
    def test_a_worse_score_is_reported(self, _name: str, change: dict, expected: str) -> None:
        worse, better = compare_scores(scores(), scores(**change))
        self.assertIn(expected, worse)
        self.assertEqual(better, [])

    def test_a_better_score_is_reported(self) -> None:
        worse, better = compare_scores(scores(), scores(word_error_rate=0.02, words_missing=0))
        self.assertEqual(worse, [])
        self.assertIn("OCR page 1 word error rate: 0.1 -> 0.02", better)

    def test_a_change_inside_the_tolerance_is_ignored(self) -> None:
        worse, better = compare_scores(scores(), scores(word_error_rate=0.105, reading_order=0.99))
        self.assertEqual((worse, better), ([], []))

    def test_only_pages_scored_on_both_sides_are_compared(self) -> None:
        other = {"pages": {"2": scores()["pages"]["1"]}}
        self.assertEqual(compare_scores(scores(), other), ([], []))


class TranscriptionFixtureTests(SimpleTestCase):
    """Guards the readings themselves, with no OCR. A reading that drifts (a typo, a
    renamed slice) would make every later score wrong without anyone noticing.
    """

    @parameterized.expand([(path.stem, path) for path in TRUTH_FILES])
    def test_each_transcription_belongs_to_a_slice_and_says_how_it_was_made(
        self, _name: str, path: Path
    ) -> None:
        truth = load_truth(path)
        slice_pdf = FIXTURES / "edge-cases" / f"{path.stem}.pdf"

        self.assertTrue(slice_pdf.is_file(), f"no slice named {slice_pdf.name}")
        self.assertTrue(truth["provenance"].strip())
        with fitz.open(slice_pdf) as doc:
            for number, text in truth["pages"].items():
                self.assertTrue(1 <= int(number) <= doc.page_count, f"page {number} not in slice")
                self.assertGreater(len(text.split()), 5, f"page {number} is almost empty")

    @parameterized.expand(
        [
            # Pages with a text layer. A reading must still match it, so a changed reading
            # shows up here. Page 46's layer lists table cells in a different order from
            # reading by row, so it is held to the same words and numbers, not the order.
            ("minutes_page_exact", "boc1020-pages2-4-upgrade-diff", "3", True),
            ("table_page_same_words_and_numbers", "boc1020-pages45-50-upgrade-diff", "2", False),
        ]
    )
    def test_a_reading_of_a_page_with_a_text_layer_still_matches_that_layer(
        self, _name: str, stem: str, page: str, same_order: bool
    ) -> None:
        score = score_pdf(
            FIXTURES / "edge-cases" / f"{stem}.pdf", FIXTURES / "ocr_truth" / f"{stem}.json"
        )["pages"][page]

        self.assertEqual((score["words_missing"], score["words_extra"]), (0, 0))
        self.assertEqual((score["numbers_missing"], score["numbers_extra"]), (0, 0))
        if same_order:
            self.assertEqual((score["word_error_rate"], score["reading_order"]), (0.0, 1.0))

    @parameterized.expand(
        [
            ("scanned_letter", "boc1020-pages24-27-upgrade-diff", 1),
            ("scanned_infographic", "boc1020-pages110-111-scanned", 1),
        ]
    )
    def test_a_scanned_page_really_has_no_text_to_copy_from(
        self, _name: str, stem: str, page: int
    ) -> None:
        with fitz.open(FIXTURES / "edge-cases" / f"{stem}.pdf") as doc:
            self.assertEqual(doc[page - 1].get_text().strip(), "")
