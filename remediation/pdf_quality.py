"""Repeatable quality report for a remediated PDF, and a comparison of two reports.

`check_ocr_regression` only fingerprints text, metadata and tag counts. This measures
what it misses: headings, lists, tables, figure alt text, repeated text, blank pages,
and (when given) the original PDF to compare the text against. Same PDF in, same
report out, so two runs (two library versions) can be compared.

Headings, lists and tables are counted per page. Heading *text* is not recorded: the
PDF only links a tag to its text through font-encoded content streams, which is not
reliable to decode here. The comparison names the pages that changed, so a person can
open them.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path
from typing import Any

import pikepdf
import pymupdf as fitz

HEADING_TAGS = {"/H", "/H1", "/H2", "/H3", "/H4", "/H5", "/H6"}
# Alt text such as "image 6" or "Figure 2" says nothing to a screen reader user.
PLACEHOLDER_ALT = re.compile(r"^(image|figure|picture|img)?\s*\d*$", re.IGNORECASE)
PHRASE_LENGTH = 5

# Report keys under "totals" where a higher number is worse, and where a lower one is.
WORSE_IF_HIGHER = (
    "empty_pages",
    "figures_without_alt",
    "figures_with_placeholder_alt",
    "tables_without_headers",
    "heading_level_skips",
    "repeated_phrases",
    "words_missing_from_source",
)
WORSE_IF_LOWER = ("headings",)
# Shown in a comparison, but a change is not automatically bad (more lists might be a
# split list or a newly found list), so it never fails a check on its own.
NEUTRAL = ("words", "lists", "list_items", "tables", "figures", "links", "words_added_to_source")


def _kids(node: pikepdf.Object) -> list[Any]:
    kids = node.get("/K")
    if kids is None:
        return []
    return list(kids) if isinstance(kids, pikepdf.Array) else [kids]


@dataclass
class _PageStructure:
    roles: list[str] = field(default_factory=list)
    heading_levels: list[int] = field(default_factory=list)
    list_sizes: list[int] = field(default_factory=list)
    tables: int = 0
    header_cells: int = 0
    tables_without_headers: int = 0
    figures: int = 0
    figures_without_alt: int = 0
    figures_with_placeholder_alt: int = 0
    links: int = 0


def _heading_level(tag: str) -> int:
    return int(tag[2]) if len(tag) == 3 and tag[2].isdigit() else 1


def _structure_by_page(pdf: pikepdf.Pdf) -> tuple[dict[int, _PageStructure], list[int]]:
    """Walks the structure tree once. Returns per-page counts and the document-order
    list of heading levels (for spotting a jump such as H1 to H3).
    """
    pages = {n: _PageStructure() for n in range(1, len(pdf.pages) + 1)}
    order: list[int] = []
    root = pdf.Root.get("/StructTreeRoot")
    if root is None:
        return pages, order
    numbers: dict[Any, int] = {
        page.objgen: number for number, page in enumerate(pdf.pages, start=1)
    }

    def visit(node: pikepdf.Dictionary, page: int | None) -> int:
        """Returns how many TH cells sit under `node`, so a Table can tell if it has any."""
        tag = str(node.get("/S", ""))
        page_ref = node.get("/Pg")
        if page_ref is not None:
            page = numbers.get(page_ref.objgen) or page
        target = pages.get(page) if page is not None else None
        if tag and target is not None:
            target.roles.append(tag)
            if tag in HEADING_TAGS:
                target.heading_levels.append(_heading_level(tag))
                order.append(_heading_level(tag))
            elif tag == "/L":
                target.list_sizes.append(
                    sum(
                        1
                        for k in _kids(node)
                        if isinstance(k, pikepdf.Dictionary) and str(k.get("/S", "")) == "/LI"
                    )
                )
            elif tag == "/Table":
                target.tables += 1
            elif tag == "/Figure":
                target.figures += 1
                alt = str(node.get("/Alt", "")).strip()
                if not alt:
                    target.figures_without_alt += 1
                elif PLACEHOLDER_ALT.match(alt):
                    target.figures_with_placeholder_alt += 1
            elif tag == "/Link":
                target.links += 1
        headers = 1 if tag == "/TH" else 0
        for kid in _kids(node):
            if isinstance(kid, pikepdf.Dictionary):
                headers += visit(kid, page)
        if target is not None:
            if tag == "/TH":
                target.header_cells += 1
            elif tag == "/Table" and headers == 0:
                target.tables_without_headers += 1
        return headers

    for kid in _kids(root):
        if isinstance(kid, pikepdf.Dictionary):
            visit(kid, None)
    return pages, order


def _page_words(doc: fitz.Document) -> list[list[str]]:
    return [[word[4] for word in doc[i].get_text("words")] for i in range(doc.page_count)]


def _phrases(words: list[str]) -> Counter[tuple[str, ...]]:
    return Counter(
        tuple(words[i : i + PHRASE_LENGTH]) for i in range(len(words) - PHRASE_LENGTH + 1)
    )


def _text_against_source(words: list[str], source_words: list[str]) -> tuple[int, int, int]:
    """Returns (repeated phrases, words missing from source, words not in source).

    A "repeated phrase" is a run of five words that is on the same page of the original
    but occurs more often in the output: OCR text laid down twice, for example. Text that
    is merely re-flowed (new word order) is not counted; it shows up in the word counts.
    """
    source_phrases = _phrases(source_words)
    repeated = sum(
        count - source_phrases[phrase]
        for phrase, count in _phrases(words).items()
        if count > source_phrases[phrase] > 0
    )
    missing = sum((Counter(source_words) - Counter(words)).values())
    added = sum((Counter(words) - Counter(source_words)).values())
    return repeated, missing, added


def _document_properties(pdf: pikepdf.Pdf) -> dict[str, Any]:
    mark_info = pdf.Root.get("/MarkInfo")
    viewer_prefs = pdf.Root.get("/ViewerPreferences")
    with pdf.open_outline() as outline:
        outline_entries = len(outline.root)
    return {
        "title": str(pdf.docinfo.get("/Title", "")),
        "language": str(pdf.Root.get("/Lang", "")),
        "marked": bool(mark_info is not None and mark_info.get("/Marked", False)),
        "display_doc_title": bool(
            viewer_prefs is not None and viewer_prefs.get("/DisplayDocTitle", False)
        ),
        "all_pages_tab_by_structure": all(str(page.get("/Tabs", "")) == "/S" for page in pdf.pages),
        "outline_entries": outline_entries,
        "link_annotations": sum(
            1
            for page in pdf.pages
            for annot in page.get("/Annots", [])
            if str(annot.get("/Subtype", "")) == "/Link"
        ),
    }


def analyze_pdf(pdf_path: Path, *, source_path: Path | None = None) -> dict[str, Any]:
    """Builds the report for `pdf_path`. Pass the original as `source_path` to also
    measure repeated and lost text (the two files must have the same pages).
    """
    with pikepdf.open(pdf_path) as pdf, fitz.open(pdf_path) as doc:
        structure, heading_order = _structure_by_page(pdf)
        words = _page_words(doc)
        document = _document_properties(pdf)

    source_words = None
    if source_path is not None:
        with fitz.open(source_path) as source_doc:
            source_words = _page_words(source_doc)
        if len(source_words) != len(words):
            raise ValueError(
                f"{source_path} has {len(source_words)} pages but {pdf_path} has {len(words)}"
            )

    role_counts: Counter[str] = Counter()
    pages: dict[str, Any] = {}
    for number, page_words in enumerate(words, start=1):
        page_structure = structure[number]
        role_counts.update(page_structure.roles)
        entry: dict[str, Any] = {
            "words": len(page_words),
            "headings": page_structure.heading_levels,
            "lists": page_structure.list_sizes,
            "tables": page_structure.tables,
            "figures": page_structure.figures,
            "tag_signature": hashlib.sha1(" ".join(page_structure.roles).encode()).hexdigest()[:10],
        }
        if source_words is not None:
            repeated, missing, added = _text_against_source(page_words, source_words[number - 1])
            entry.update(
                repeated_phrases=repeated,
                words_missing_from_source=missing,
                words_added_to_source=added,
            )
        pages[str(number)] = entry

    all_structure = list(structure.values())
    totals: dict[str, Any] = {
        "words": sum(len(w) for w in words),
        "empty_pages": sum(1 for w in words if len(w) < 5),
        "headings": sum(len(p.heading_levels) for p in all_structure),
        "heading_level_skips": sum(
            1 for before, after in pairwise(heading_order) if after - before > 1
        ),
        "lists": sum(len(p.list_sizes) for p in all_structure),
        "list_items": sum(sum(p.list_sizes) for p in all_structure),
        "tables": sum(p.tables for p in all_structure),
        "tables_without_headers": sum(p.tables_without_headers for p in all_structure),
        "figures": sum(p.figures for p in all_structure),
        "figures_without_alt": sum(p.figures_without_alt for p in all_structure),
        "figures_with_placeholder_alt": sum(p.figures_with_placeholder_alt for p in all_structure),
        "links": sum(p.links for p in all_structure),
    }
    if source_words is not None:
        for key in ("repeated_phrases", "words_missing_from_source", "words_added_to_source"):
            totals[key] = sum(entry[key] for entry in pages.values())

    return {
        "file": pdf_path.name,
        "page_count": len(words),
        "document": document,
        "totals": totals,
        "tag_counts": dict(sorted(role_counts.items())),
        "pages": pages,
        "verapdf": None,
        "stages": None,
    }


@dataclass
class Comparison:
    """`baseline` is the current stack, `candidate` the one being tested."""

    rows: list[tuple[str, Any, Any]] = field(default_factory=list)
    regressions: list[str] = field(default_factory=list)
    improvements: list[str] = field(default_factory=list)
    page_changes: list[str] = field(default_factory=list)


def _page_changes(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    changes = []
    if len(after["headings"]) != len(before["headings"]):
        changes.append(f"headings {len(before['headings'])} -> {len(after['headings'])}")
    elif after["headings"] != before["headings"]:
        changes.append(f"heading levels {before['headings']} -> {after['headings']}")
    if after["lists"] != before["lists"]:
        changes.append(f"list sizes {before['lists']} -> {after['lists']}")
    for key in ("tables", "figures", "words"):
        if after[key] != before[key]:
            changes.append(f"{key} {before[key]} -> {after[key]}")
    if not changes and after["tag_signature"] != before["tag_signature"]:
        changes.append("tag order changed")
    return changes


def compare_reports(baseline: dict[str, Any], candidate: dict[str, Any]) -> Comparison:
    result = Comparison()

    for key in (*WORSE_IF_HIGHER, *WORSE_IF_LOWER, *NEUTRAL):
        if key not in baseline["totals"] or key not in candidate["totals"]:
            continue
        before, after = baseline["totals"][key], candidate["totals"][key]
        result.rows.append((key, before, after))
        if before == after:
            continue
        line = f"{key}: {before} -> {after}"
        if key in WORSE_IF_HIGHER:
            (result.regressions if after > before else result.improvements).append(line)
        elif key in WORSE_IF_LOWER:
            (result.regressions if after < before else result.improvements).append(line)

    if baseline.get("verapdf") is not None and candidate.get("verapdf") is not None:
        result.rows.append(
            (
                "verapdf failed checks",
                sum(baseline["verapdf"].values()),
                sum(candidate["verapdf"].values()),
            )
        )
        for rule in sorted(set(baseline["verapdf"]) | set(candidate["verapdf"])):
            before, after = baseline["verapdf"].get(rule, 0), candidate["verapdf"].get(rule, 0)
            if after > before:
                result.regressions.append(f"veraPDF {rule}: {before} -> {after}")
            elif after < before:
                result.improvements.append(f"veraPDF {rule}: {before} -> {after}")

    for key, before in baseline["document"].items():
        after = candidate["document"].get(key)
        if before != after:
            result.regressions.append(f"document {key}: {before!r} -> {after!r}")

    for number in sorted(baseline["pages"], key=int):
        after_page = candidate["pages"].get(number)
        if after_page is None:
            continue
        changes = _page_changes(baseline["pages"][number], after_page)
        if changes:
            result.page_changes.append(f"page {number}: " + "; ".join(changes))
    return result
