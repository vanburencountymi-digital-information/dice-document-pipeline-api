"""Scores a PDF's text against a transcription made by reading the page images.

The original's text layer can't be the answer for scanned pages (it has none) and can be
wrong for others (fonts with no Unicode map). So the answer is a transcription of what is
visible on the page, written by reading a rendering of the page, saved as a JSON file:

    {"provenance": "who/what read it, and how it was checked",
     "pages": {"1": "text in reading order", ...}}

Page numbers count from 1 within the PDF being scored (a slice, not the whole packet).

Three kinds of check, each on its own page where it matters most:
- numbers: every number in the transcription is in the output, same count (tables)
- reading order: shared words come out in the same order (pages with columns or blocks)
- words: word and character error rate, and a count of missing and extra words that
  ignores order (same word count, different words)

A transcription can itself be wrong. Check it against the text layer where there is one,
and have a person check it where there isn't, before trusting a failure.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from collections.abc import Sequence
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import pymupdf as fitz

# A change smaller than this is treated as noise, not a difference.
RATE_TOLERANCE = 0.01
ORDER_TOLERANCE = 0.02

_PUNCTUATION_FIXES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-"})
_NUMBER = re.compile(r"\d")


def normalize_words(text: str) -> list[str]:
    """Splits into words after making curly quotes, dashes and look-alike characters plain,
    so the same text typeset two ways is not scored as different.
    """
    return unicodedata.normalize("NFKC", text).translate(_PUNCTUATION_FIXES).split()


def edit_distance(a: Sequence[str], b: Sequence[str]) -> int:
    """Fewest single-item changes (swap, drop, add) to turn `a` into `b`."""
    previous = list(range(len(b) + 1))
    for i, item_a in enumerate(a, start=1):
        current = [i]
        for j, item_b in enumerate(b, start=1):
            current.append(
                min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (item_a != item_b))
            )
        previous = current
    return previous[-1]


def error_rate(truth: Sequence[str], output: Sequence[str]) -> float:
    if not truth:
        return 0.0 if not output else 1.0
    return edit_distance(truth, output) / len(truth)


def numbers_in(words: Sequence[str]) -> Counter[str]:
    """Every word with a digit in it, minus punctuation round the edges ('$1,427.' counts
    as '$1,427'). A wrong digit is a different number.
    """
    return Counter(word.strip(".,;:()[]\"'") for word in words if _NUMBER.search(word))


def reading_order_score(truth: Sequence[str], output: Sequence[str]) -> float:
    """1.0 if the words the two share come out in the same order, lower as they scramble.
    Words missing from one side don't lower it (that is the word checks' job).
    """
    shared = sum((Counter(truth) & Counter(output)).values())
    if shared == 0:
        return 1.0 if not truth and not output else 0.0
    matched = SequenceMatcher(None, truth, output, autojunk=False).ratio()
    best_possible = 2 * shared / (len(truth) + len(output))
    return min(1.0, matched / best_possible)


def score_page(truth_text: str, output_text: str) -> dict[str, Any]:
    truth, output = normalize_words(truth_text), normalize_words(output_text)
    missing_words = Counter(truth) - Counter(output)
    extra_words = Counter(output) - Counter(truth)
    missing_numbers = numbers_in(truth) - numbers_in(output)
    extra_numbers = numbers_in(output) - numbers_in(truth)
    return {
        "truth_words": len(truth),
        "output_words": len(output),
        "word_error_rate": round(error_rate(truth, output), 4),
        "char_error_rate": round(error_rate(" ".join(truth), " ".join(output)), 4),
        "words_missing": sum(missing_words.values()),
        "words_extra": sum(extra_words.values()),
        "numbers_missing": sum(missing_numbers.values()),
        "numbers_extra": sum(extra_numbers.values()),
        "reading_order": round(reading_order_score(truth, output), 4),
        # What is missing or extra, so a person can see it without re-running anything.
        "example_missing": sorted(missing_words)[:10],
        "example_extra": sorted(extra_words)[:10],
    }


def load_truth(path: Path) -> dict[str, Any]:
    truth = json.loads(path.read_text())
    if "pages" not in truth:
        raise ValueError(f"{path} has no 'pages'")
    return truth


def score_pdf(pdf_path: Path, truth_path: Path) -> dict[str, Any]:
    """Scores each page that has a transcription. Output text is read in the order the
    PDF stores it, which for a tagged PDF is the order a screen reader gets.
    """
    truth = load_truth(truth_path)
    pages: dict[str, Any] = {}
    with fitz.open(pdf_path) as doc:
        for number, truth_text in truth["pages"].items():
            index = int(number) - 1
            if not 0 <= index < doc.page_count:
                raise ValueError(f"{truth_path} has page {number}, {pdf_path} has {doc.page_count}")
            words = " ".join(word[4] for word in doc[index].get_text("words"))
            pages[number] = score_page(truth_text, words)
    return {"provenance": truth.get("provenance", ""), "pages": pages}


def compare_scores(
    baseline: dict[str, Any], candidate: dict[str, Any]
) -> tuple[list[str], list[str]]:
    """Returns (worse, better) lines for the pages both sides scored."""
    worse: list[str] = []
    better: list[str] = []
    for number in sorted(set(baseline["pages"]) & set(candidate["pages"]), key=int):
        before, after = baseline["pages"][number], candidate["pages"][number]
        checks = (
            ("word error rate", "word_error_rate", RATE_TOLERANCE, True),
            ("char error rate", "char_error_rate", RATE_TOLERANCE, True),
            ("words missing", "words_missing", 0, True),
            ("words extra", "words_extra", 0, True),
            ("numbers missing", "numbers_missing", 0, True),
            ("numbers extra", "numbers_extra", 0, True),
            ("reading order", "reading_order", ORDER_TOLERANCE, False),
        )
        for label, key, tolerance, higher_is_worse in checks:
            change = after[key] - before[key]
            if abs(change) <= tolerance:
                continue
            line = f"OCR page {number} {label}: {before[key]} -> {after[key]}"
            (worse if (change > 0) == higher_is_worse else better).append(line)
    return worse, better
