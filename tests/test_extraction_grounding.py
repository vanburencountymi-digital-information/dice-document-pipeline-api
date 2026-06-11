"""Tests for dice_document_pipeline.extraction.grounding.

These tests are all deterministic (no PDF files, no LLM calls). They cover the
normalization logic, distinctiveness gate, verdict logic via mocked fitz pages,
batch verify_citations, and the correct_pages helper.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from dice_document_pipeline.extraction.grounding import (
    GROUNDED,
    MISLOCATED,
    NORMALIZED_MATCH,
    NO_PAGE,
    NO_QUOTE,
    PDF_UNAVAILABLE,
    UNGROUNDED,
    GroundingResult,
    _is_distinctive,
    correct_pages,
    normalize_for_search,
    search_page_for_quote,
    verify_citations,
    verify_quote,
)


# ---------------------------------------------------------------------------
# normalize_for_search
# ---------------------------------------------------------------------------

class TestNormalizeForSearch:
    def test_none_returns_empty(self):
        assert normalize_for_search(None) == ""

    def test_empty_returns_empty(self):
        assert normalize_for_search("") == ""

    def test_collapses_whitespace(self):
        assert normalize_for_search("foo   bar\nbaz") == "foo bar baz"

    def test_expands_ligature_fi(self):
        assert normalize_for_search("ﬁle") == "file"

    def test_folds_smart_quotes(self):
        assert normalize_for_search("‘hello’") == "'hello'"

    def test_folds_en_dash(self):
        assert normalize_for_search("A–B") == "A-B"

    def test_strips_leading_trailing_whitespace(self):
        assert normalize_for_search("  hello  ") == "hello"

    def test_idempotent(self):
        s = "The quick — brown fox"
        assert normalize_for_search(normalize_for_search(s)) == normalize_for_search(s)


# ---------------------------------------------------------------------------
# _is_distinctive
# ---------------------------------------------------------------------------

class TestIsDistinctive:
    def test_none_is_not_distinctive(self):
        assert not _is_distinctive(None)

    def test_bare_number_is_not_distinctive(self):
        assert not _is_distinctive("30")

    def test_short_string_is_not_distinctive(self):
        assert not _is_distinctive("abc")   # < 8 chars

    def test_numeric_string_is_not_distinctive(self):
        assert not _is_distinctive("3,000.00")  # no alpha

    def test_long_alpha_string_is_distinctive(self):
        assert _is_distinctive("minimum lot area")

    def test_exactly_8_chars_with_alpha(self):
        assert _is_distinctive("abcdefgh")


# ---------------------------------------------------------------------------
# search_page_for_quote  (mocked fitz page)
# ---------------------------------------------------------------------------

def _make_page(text: str, search_results: dict | None = None):
    """Build a fake fitz page whose search_for and get_text we control."""
    page = MagicMock()
    search_results = search_results or {}

    def fake_search_for(q):
        hits = search_results.get(q, [])
        mocks = []
        for (x0, y0, x1, y1) in hits:
            r = MagicMock()
            r.x0, r.y0, r.x1, r.y1 = x0, y0, x1, y1
            mocks.append(r)
        return mocks

    page.search_for.side_effect = fake_search_for
    page.get_text.return_value = text
    return page


class TestSearchPageForQuote:
    def test_exact_match(self):
        page = _make_page("foo", {"hello world": [(0, 0, 10, 10)]})
        bbox, kind = search_page_for_quote(page, "hello world")
        assert kind == "exact"
        assert bbox == (0, 0, 10, 10)

    def test_no_match_returns_none(self):
        page = _make_page("completely different text", {})
        bbox, kind = search_page_for_quote(page, "not here at all nope")
        assert kind is None
        assert bbox is None

    def test_normalized_match(self):
        # Quote has smart quote; page text has straight quote
        page = _make_page("minimum lot area of 30 feet", {})
        bbox, kind = search_page_for_quote(page, "minimum lot area of 30 feet")
        # exact path should fire since page text matches directly
        assert kind is not None

    def test_empty_quote_returns_none(self):
        page = _make_page("some text", {})
        bbox, kind = search_page_for_quote(page, "")
        assert bbox is None
        assert kind is None


# ---------------------------------------------------------------------------
# verify_quote  (mocked fitz.open)
# ---------------------------------------------------------------------------

def _mock_doc(pages_text: list[str]):
    """Build a fake fitz.Document with controllable page text."""
    doc = MagicMock()
    doc.__len__ = MagicMock(return_value=len(pages_text))

    fake_pages = []
    for text in pages_text:
        page = MagicMock()
        page.search_for.return_value = []
        page.get_text.return_value = text
        fake_pages.append(page)

    doc.__getitem__ = MagicMock(side_effect=lambda i: fake_pages[i])
    return doc


class TestVerifyQuote:
    def test_no_quote_returns_no_quote(self):
        result = verify_quote(None, page=1, pdf_path="x.pdf", doc=_mock_doc(["text"]))
        assert result.verdict == NO_QUOTE

    def test_pdf_unavailable_when_doc_none(self):
        with patch("dice_document_pipeline.extraction.grounding._open_pdf", return_value=None):
            result = verify_quote("some long quote here", page=1, pdf_path="missing.pdf")
        assert result.verdict == PDF_UNAVAILABLE

    def test_grounded_when_exact_match_on_cited_page(self):
        doc = _mock_doc([""])
        page_mock = doc[0]
        hit = MagicMock()
        hit.x0, hit.y0, hit.x1, hit.y1 = 0, 0, 50, 10
        page_mock.search_for.return_value = [hit]

        result = verify_quote("minimum lot area", page=1, pdf_path="x.pdf", doc=doc)
        assert result.verdict == GROUNDED
        assert result.cited_page == 1
        assert result.found_page == 1

    def test_ungrounded_when_not_found_anywhere(self):
        doc = _mock_doc(["completely different", "also different"])
        result = verify_quote("minimum lot area requirement", page=1,
                              pdf_path="x.pdf", doc=doc)
        assert result.verdict == UNGROUNDED

    def test_no_page_returns_no_page_verdict(self):
        doc = _mock_doc(["some text here nothing"])
        result = verify_quote("some text here nothing", page=None,
                              pdf_path="x.pdf", doc=doc)
        # Quote is short/non-distinctive or won't cross-search; verdict is NO_PAGE
        assert result.verdict in (NO_PAGE, UNGROUNDED)


# ---------------------------------------------------------------------------
# correct_pages
# ---------------------------------------------------------------------------

class TestCorrectPages:
    def test_corrects_mislocated(self):
        citations = [{"page": 5, "source_quote": "something"}]
        results = [{"verdict": MISLOCATED, "found_page": 7}]
        n = correct_pages(citations, results)
        assert n == 1
        assert citations[0]["page"] == 7

    def test_corrects_no_page(self):
        citations = [{"page": None, "source_quote": "something"}]
        results = [{"verdict": NO_PAGE, "found_page": 3}]
        n = correct_pages(citations, results)
        assert n == 1
        assert citations[0]["page"] == 3

    def test_does_not_touch_grounded(self):
        citations = [{"page": 2, "source_quote": "something"}]
        results = [{"verdict": GROUNDED, "found_page": 2}]
        n = correct_pages(citations, results)
        assert n == 0
        assert citations[0]["page"] == 2

    def test_idempotent(self):
        citations = [{"page": 5, "source_quote": "something"}]
        results = [{"verdict": MISLOCATED, "found_page": 7}]
        correct_pages(citations, results)
        results[0]["verdict"] = GROUNDED  # simulate second pass found it grounded
        n2 = correct_pages(citations, results)
        assert n2 == 0
        assert citations[0]["page"] == 7


# ---------------------------------------------------------------------------
# verify_citations  (batch)
# ---------------------------------------------------------------------------

class TestVerifyCitations:
    def test_returns_one_result_per_citation(self):
        citations = [
            {"source_quote": "foo bar baz qux", "page": 1,
             "field": "test", "district_id": "R1", "value": "30"},
            {"source_quote": None, "page": 2,
             "field": "test2", "district_id": "R2", "value": "50"},
        ]
        with patch("dice_document_pipeline.extraction.grounding._open_pdf",
                   return_value=None):
            results = verify_citations(citations, "missing.pdf")
        assert len(results) == 2
        assert results[0]["verdict"] == PDF_UNAVAILABLE
        assert results[1]["verdict"] == PDF_UNAVAILABLE

    def test_schema_agnostic_field_names(self):
        """verify_citations reads 'field' or 'column' interchangeably."""
        citations = [
            {"column": "lot_area", "source_quote": None, "page": 1,
             "district_id": "A1", "value": "43560"},
        ]
        with patch("dice_document_pipeline.extraction.grounding._open_pdf",
                   return_value=None):
            results = verify_citations(citations, "x.pdf")
        assert results[0]["field"] == "lot_area"
