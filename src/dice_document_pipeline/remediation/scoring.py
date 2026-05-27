"""Compliance scoring for remediated PDF documents."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

DEDUCTIONS: dict[str, int] = {
    "no_structure_tree": 18,
    "no_text_layer": 20,
    "missing_alt_text": 12,
    "form_no_tooltips": 10,
    "missing_headings": 10,
    "missing_bookmarks": 6,
    "unembedded_fonts": 6,
    "vague_hyperlinks": 6,
    "color_contrast": 5,
    "complex_tables": 4,
}

GRADE_THRESHOLDS: list[tuple[int, str]] = [
    (90, "A"),
    (75, "B"),
    (60, "C"),
    (40, "D"),
    (0, "F"),
]


def score_document(
    issues: Mapping[str, Any], page_count: int
) -> tuple[int, str, list[str]]:
    """Calculate a 0-100 compliance score and derive a letter grade."""
    score = 100
    manual_items: list[str] = []

    if not issues.get("has_structure_tree"):
        score -= DEDUCTIONS["no_structure_tree"]
        manual_items.append(
            "Structure tree missing: open in Acrobat Pro "
            "-> Tools -> Accessibility -> Autotag Document (for full MCID tagging)"
        )

    if not issues.get("has_text_layer"):
        score -= DEDUCTIONS["no_text_layer"]
        manual_items.append(
            "No text layer: open in Acrobat Pro "
            "-> Tools -> Scan & OCR -> Recognize Text -> In This File"
        )

    if issues.get("images_without_alt", 0) > 0 and issues.get("has_images"):
        score -= DEDUCTIONS["missing_alt_text"]
        image_count = issues["images_without_alt"]
        manual_items.append(
            f"{image_count} image(s) still missing alt text: open in Acrobat Pro "
            "-> Tags panel -> right-click each Figure element -> Properties -> Alt Text"
        )

    if issues.get("form_fields_without_tooltips", 0) > 0:
        field_count = issues["form_fields_without_tooltips"]
        score -= DEDUCTIONS["form_no_tooltips"]
        manual_items.append(
            f"{field_count} form field(s) still missing tooltip text: open in Acrobat Pro "
            "-> Tools -> Prepare Form -> right-click each field -> Properties -> Tooltip "
            "(pipeline set /TU = field name as a baseline; replace with plain-English label)"
        )

    if not issues.get("has_headings") and page_count > 10:
        score -= DEDUCTIONS["missing_headings"]
        manual_items.append(
            "No heading structure detected: open in Acrobat Pro "
            "-> Tags panel -> re-tag content using H1/H2/H3 structure elements"
        )

    if not issues.get("has_bookmarks") and page_count > 9:
        score -= DEDUCTIONS["missing_bookmarks"]
        manual_items.append(
            f"No bookmarks on {page_count}-page document: open in Acrobat Pro "
            "-> Tools -> Edit PDF -> Bookmarks -> New Bookmark from Structure"
        )

    unembedded = issues.get("unembedded_fonts", [])
    if unembedded:
        score -= DEDUCTIONS["unembedded_fonts"]
        font_list = ", ".join(unembedded[:3]) + ("..." if len(unembedded) > 3 else "")
        manual_items.append(
            f"Possibly unembedded fonts ({font_list}): open in Acrobat Pro "
            "-> Tools -> Print Production -> Preflight -> Embed fonts profile"
        )

    if issues.get("vague_links"):
        score -= DEDUCTIONS["vague_hyperlinks"]
        manual_items.append(
            "Vague hyperlink text detected: review links with text like 'click here', "
            "'here', or 'read more' - replace with descriptive anchor text"
        )

    if not issues.get("contrast_check_passed"):
        score -= DEDUCTIONS["color_contrast"]
        manual_items.append(
            "Color contrast issue detected: confirm 4.5:1 minimum "
            "ratio using Acrobat Accessibility Checker or the free Color Contrast Analyzer tool"
        )

    if issues.get("tables_without_headers", 0) > 0:
        table_count = issues["tables_without_headers"]
        score -= DEDUCTIONS["complex_tables"]
        manual_items.append(
            f"{table_count} table(s) still need header review: open in Acrobat Pro "
            "-> Tags panel -> verify TH/TD structure and /Scope on header cells "
            "(pipeline promoted first-row TDs to TH as a baseline)"
        )

    score = max(0, score)

    grade = "F"
    for threshold, letter in GRADE_THRESHOLDS:
        if score >= threshold:
            grade = letter
            break

    return score, grade, manual_items
