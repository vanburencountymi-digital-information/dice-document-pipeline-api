#!/usr/bin/env python3
"""Source-grounding verifier -- round-trips a citation's quote back to the PDF.

Lifted from mzm-extraction-pipeline/scripts/source_validation.py. That module
was intentionally written to be dependency-light and schema-agnostic so it could
move here. MZM still uses its own local copy until it migrates to import from
here.

The load-bearing check: does the text a citation CLAIMS to quote actually appear,
verbatim, on the page it cites? This is the deterministic kernel -- stdlib +
PyMuPDF (fitz) only, no project-specific schemas. Vision escalation for scanned
PDFs lives one layer up in grounding_report.py (MZM) and should be wired
similarly in any consumer.

Verdicts (plain str so callers need not import an enum):
  GROUNDED          quote found verbatim on the cited page
  NORMALIZED_MATCH  found on the cited page only after normalization
                    (whitespace/ligature/smart-quote folding) -- still a real
                    match, slightly softer confidence
  MISLOCATED        quote found, but on a DIFFERENT page than cited
  UNGROUNDED        quote not found anywhere in the document
  NO_QUOTE          the citation carries no source_quote to check
  NO_PAGE           quote present but no page cited (and not found doc-wide)
  PDF_UNAVAILABLE   PyMuPDF missing or the PDF could not be opened
  VISION_GROUNDED / VISION_UNGROUNDED  set by a caller-supplied escalation
                    layer, never by this kernel

Usage
-----
    from dice_document_pipeline.extraction.grounding import verify_quote, verify_citations

    # Single citation
    result = verify_quote(source_quote="35 feet", page=12, pdf_path="doc.pdf")
    print(result.verdict)  # GROUNDED / MISLOCATED / UNGROUNDED / ...

    # Batch (one PDF open, many citations)
    results = verify_citations(citations=[{"source_quote": ..., "page": ...}],
                               pdf_path="doc.pdf")
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, asdict

try:
    import fitz  # PyMuPDF
    _FITZ_AVAILABLE = True
except ImportError:  # pragma: no cover - environment-dependent
    _FITZ_AVAILABLE = False


# ---------------------------------------------------------------------------
# Verdict constants
# ---------------------------------------------------------------------------

GROUNDED = "GROUNDED"
NORMALIZED_MATCH = "NORMALIZED_MATCH"
MISLOCATED = "MISLOCATED"
UNGROUNDED = "UNGROUNDED"
NO_QUOTE = "NO_QUOTE"
NO_PAGE = "NO_PAGE"
PDF_UNAVAILABLE = "PDF_UNAVAILABLE"
# Set by a caller-supplied vision escalation layer, never by this kernel.
VISION_GROUNDED = "VISION_GROUNDED"
VISION_UNGROUNDED = "VISION_UNGROUNDED"

# Verdicts that mean "the document really says this".
GROUNDED_VERDICTS = frozenset({GROUNDED, NORMALIZED_MATCH, VISION_GROUNDED})
# Verdicts a confidence gate should treat as "claimed but not verified".
UNGROUNDED_VERDICTS = frozenset({UNGROUNDED, MISLOCATED, VISION_UNGROUNDED})


@dataclass
class GroundingResult:
    verdict: str
    cited_page: int | None = None   # page the citation claimed (1-indexed)
    found_page: int | None = None   # page the quote was actually located on
    bbox: tuple | None = None       # (x0, y0, x1, y1) in PDF points, if found
    match_kind: str | None = None   # "exact" | "normalized" | "partial" | None
    detail: str = ""                # one-line explanation

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Normalization (pure)
# ---------------------------------------------------------------------------

_LIGATURES = {
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl",
    "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st",
}
_QUOTES = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"',
    "′": "'", "″": '"', "´": "'", "`": "'",
}
_DASHES = {"–": "-", "—": "-", "−": "-", "‐": "-", "‑": "-"}
_FOLD = {**_LIGATURES, **_QUOTES, **_DASHES}
_WS_RE = re.compile(r"\s+")


def normalize_for_search(text: str | None) -> str:
    """Fold a string to a search-stable form.

    Applies NFKC normalisation, expands ligatures, folds smart quotes and
    dashes to ASCII, and collapses all whitespace runs to a single space.
    Pure and idempotent -- safe to call on both the quote and the page text.
    """
    if not text:
        return ""
    s = unicodedata.normalize("NFKC", str(text))
    for k, v in _FOLD.items():
        if k in s:
            s = s.replace(k, v)
    return _WS_RE.sub(" ", s).strip()


def cp1252_safe(text: str | None) -> str:
    """Return a cp1252-safe version of text for Windows console/log output."""
    if not text:
        return ""
    return normalize_for_search(text).encode("cp1252", "replace").decode("cp1252")


def _is_distinctive(quote: str | None) -> bool:
    """True if the quote is safe to cross-search across the whole document.

    Short or purely numeric quotes ("30", "3,000") recur throughout a document
    and would mis-locate. Requires >= 8 chars AND at least one letter.
    """
    if not quote:
        return False
    q = quote.strip()
    return len(q) >= 8 and any(ch.isalpha() for ch in q)


# ---------------------------------------------------------------------------
# Page / document search
# ---------------------------------------------------------------------------

def _rect(hit) -> tuple:
    return (hit.x0, hit.y0, hit.x1, hit.y1)


def _recover_bbox(page, quote: str) -> tuple | None:
    """Best-effort bbox for a normalized/partial hit."""
    for cand in (quote, re.sub(r"[a-zA-Z,\s]+$", "", quote.strip())):
        if cand:
            hits = page.search_for(cand)
            if hits:
                return _rect(hits[0])
    lead = re.match(r"[A-Za-z0-9][A-Za-z0-9 ,.\-]{6,40}", normalize_for_search(quote))
    if lead:
        hits = page.search_for(lead.group(0).strip())
        if hits:
            return _rect(hits[0])
    return None


def search_page_for_quote(page, quote: str) -> tuple[tuple | None, str | None]:
    """Locate `quote` on one fitz page. Returns (bbox, match_kind).

    Match order: exact -> footnote-stripped exact -> normalized substring ->
    distinctive-prefix partial. Returns (None, None) on no match.
    """
    if not quote:
        return None, None
    # 1. Exact
    hits = page.search_for(quote)
    if hits:
        return _rect(hits[0]), "exact"
    # 2. Footnote-letter-stripped exact ("35 a" -> "35")
    cleaned = re.sub(r"[a-zA-Z,\s]+$", "", quote.strip())
    if cleaned and cleaned != quote:
        hits = page.search_for(cleaned)
        if hits:
            return _rect(hits[0]), "exact"
    # 3. Normalized substring (handles smart quotes, ligatures, wrapped lines)
    nq = normalize_for_search(quote).lower()
    if not nq:
        return None, None
    ptext = normalize_for_search(page.get_text("text")).lower()
    if nq in ptext:
        return _recover_bbox(page, quote), "normalized"
    # 4. Distinctive leading prefix (paraphrase-tolerant, alpha-gated)
    if _is_distinctive(nq):
        prefix = nq[:40]
        if prefix and prefix in ptext:
            return _recover_bbox(page, quote), "partial"
    return None, None


def search_document_for_quote(
    doc,
    quote: str,
    skip_page: int | None = None,
) -> tuple[int | None, tuple | None, str | None]:
    """Scan every page for `quote`, optionally skipping one page.

    Returns (found_page_1indexed, bbox, match_kind). Only distinctive quotes
    are cross-searched -- bare numbers would mis-locate.
    """
    if not _is_distinctive(normalize_for_search(quote)):
        return None, None, None
    for i in range(len(doc)):
        pg1 = i + 1
        if skip_page is not None and pg1 == skip_page:
            continue
        bbox, kind = search_page_for_quote(doc[i], quote)
        if kind:
            return pg1, bbox, kind
    return None, None, None


# ---------------------------------------------------------------------------
# Single-citation + batch verification
# ---------------------------------------------------------------------------

def _open_pdf(pdf_path: str):
    if not _FITZ_AVAILABLE:
        return None
    try:
        return fitz.open(pdf_path)
    except Exception:
        return None


def verify_quote(
    source_quote: str | None,
    page: int | None,
    pdf_path: str,
    *,
    doc=None,
) -> GroundingResult:
    """Round-trip ONE citation's quote to the source PDF.

    Pass an already-open `doc` (fitz.Document) when verifying many citations
    against the same file to avoid repeated open/close overhead.
    """
    own_doc = None
    if doc is None:
        own_doc = _open_pdf(pdf_path)
        doc = own_doc
    if doc is None:
        return GroundingResult(
            PDF_UNAVAILABLE,
            cited_page=page,
            detail="PyMuPDF unavailable or PDF unreadable",
        )
    try:
        if not source_quote or not str(source_quote).strip():
            return GroundingResult(NO_QUOTE, cited_page=page,
                                   detail="citation carries no source_quote")
        quote = str(source_quote)
        npages = len(doc)

        # Try the cited page first.
        if page is not None and 1 <= page <= npages:
            bbox, kind = search_page_for_quote(doc[page - 1], quote)
            if kind == "exact":
                return GroundingResult(GROUNDED, page, page, bbox, kind,
                                       f"quote found verbatim on cited page {page}")
            if kind in ("normalized", "partial"):
                return GroundingResult(NORMALIZED_MATCH, page, page, bbox, kind,
                                       f"quote found on cited page {page} ({kind} match)")

        # Not on the cited page (or no/invalid page): try doc-wide.
        found_page, bbox, kind = search_document_for_quote(doc, quote, skip_page=page)
        if found_page is not None:
            if page is None:
                return GroundingResult(NO_PAGE, None, found_page, bbox, kind,
                                       f"no page cited; quote found on page {found_page}")
            return GroundingResult(MISLOCATED, page, found_page, bbox, kind,
                                   f"quote not on cited page {page}; found on {found_page}")

        if page is None:
            return GroundingResult(NO_PAGE, None, None, None, None,
                                   "no page cited and quote not located")
        return GroundingResult(UNGROUNDED, page, None, None, None,
                               f"quote NOT found on cited page {page} (or anywhere)")
    finally:
        if own_doc is not None:
            own_doc.close()


def _set_field(cit, key, value) -> None:
    if isinstance(cit, dict):
        cit[key] = value
    else:
        setattr(cit, key, value)


def correct_pages(citations, results: list[dict]) -> int:
    """Auto-correct page pointers in place using grounding results.

    For MISLOCATED or NO_PAGE citations whose quote was found on a different
    page, updates the citation's `page` field to the actual found page.
    Works on dict citations or pydantic/attr objects. Returns the count
    corrected. Idempotent.
    """
    n = 0
    for cit, res in zip(citations, results):
        found = res.get("found_page")
        if found and res.get("verdict") in (MISLOCATED, NO_PAGE):
            _set_field(cit, "page", found)
            n += 1
    return n


def verify_citations(citations: list[dict], pdf_path: str) -> list[dict]:
    """Batch-verify a list of citations against a PDF.

    Opens the PDF once and verifies every citation. Reads citation fields via
    .get() so it is schema-agnostic -- works for any project's citation dicts,
    not just MZM. Returns one result dict per citation.
    """
    doc = _open_pdf(pdf_path)
    out: list[dict] = []
    try:
        for cit in citations:
            field = cit.get("field") or cit.get("column")
            district = cit.get("district_id")
            value = cit.get("value")
            quote = cit.get("source_quote")
            page = cit.get("page")
            if doc is None:
                res = GroundingResult(PDF_UNAVAILABLE, cited_page=page,
                                     detail="PyMuPDF unavailable or PDF unreadable")
            else:
                res = verify_quote(quote, page, pdf_path, doc=doc)
            row = {
                "field": field,
                "district_id": district,
                "value": value,
                "source_quote": quote,
                "section": cit.get("section"),
                "section_id": cit.get("section_id"),
            }
            row.update(res.to_dict())
            out.append(row)
    finally:
        if doc is not None:
            doc.close()
    return out


__all__ = [
    "GROUNDED", "NORMALIZED_MATCH", "MISLOCATED", "UNGROUNDED",
    "NO_QUOTE", "NO_PAGE", "PDF_UNAVAILABLE",
    "VISION_GROUNDED", "VISION_UNGROUNDED",
    "GROUNDED_VERDICTS", "UNGROUNDED_VERDICTS",
    "GroundingResult",
    "normalize_for_search", "cp1252_safe",
    "search_page_for_quote", "search_document_for_quote",
    "verify_quote", "verify_citations", "correct_pages",
]
