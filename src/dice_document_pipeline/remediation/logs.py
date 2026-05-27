"""Remediation log formatting and writing."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any


def build_remediation_log(
    *,
    filename: str,
    issues: dict[str, Any],
    fixes: Sequence[str],
    alt_log_entries: Sequence[str],
    manual_items: Sequence[str],
    score: int,
    grade: str,
    county_name: str,
    log_date: str,
) -> str:
    """Build the human-readable remediation log body."""
    page_count = issues.get("page_count", 0)

    lines = [
        "=" * 70,
        f"ADA REMEDIATION LOG  -  {county_name}",
        "=" * 70,
        f"File             : {filename}",
        f"Date             : {log_date}",
        f"Pages            : {page_count}",
        f"Compliance Score : {score}/100",
        f"Compliance Grade : {grade}",
        "",
        "-" * 70,
        "ISSUES FOUND",
        "-" * 70,
    ]

    issue_count = 0

    def flag(label: str, severity: str, condition: bool) -> None:
        nonlocal issue_count
        if condition:
            lines.append(f"  [{severity:<8}] {label}")
            issue_count += 1

    flag(
        "No structure tree - autotag did not complete",
        "CRITICAL",
        not issues.get("has_structure_tree"),
    )
    flag(
        "No text layer - document may still be a scanned image",
        "CRITICAL",
        not issues.get("has_text_layer"),
    )

    if issues.get("images_without_alt", 0) > 0 and issues.get("has_images"):
        lines.append(f"  [HIGH    ] {issues['images_without_alt']} image(s) missing alt text")
        issue_count += 1

    if issues.get("form_fields_without_tooltips", 0) > 0:
        lines.append(
            f"  [HIGH    ] {issues['form_fields_without_tooltips']} form field(s) - "
            "tooltip set to field name; manual review recommended"
        )
        issue_count += 1

    flag(
        "Missing heading structure",
        "MEDIUM",
        not issues.get("has_headings") and page_count > 2,
    )
    flag(
        f"No bookmarks on {page_count}-page document",
        "MEDIUM",
        not issues.get("has_bookmarks") and page_count > 9,
    )

    unembedded = issues.get("unembedded_fonts", [])
    if unembedded:
        lines.append(f"  [MEDIUM  ] Possibly unembedded fonts: {', '.join(unembedded[:5])}")
        issue_count += 1

    if issues.get("vague_links"):
        lines.append(f"  [MEDIUM  ] Vague hyperlink text: {issues['vague_links'][:3]}")
        issue_count += 1

    if issues.get("contrast_check_passed"):
        lines.append("  [PASS    ] Color contrast - AI-verified on sampled pages (4.5:1 met)")
    else:
        lines.append("  [LOW     ] Color contrast requires manual verification")
        issue_count += 1

    if issues.get("tables_without_headers", 0) > 0:
        lines.append(
            f"  [LOW     ] {issues['tables_without_headers']} table(s) - "
            "first row promoted to TH; manual scope verification recommended"
        )
        issue_count += 1
    elif issues.get("has_tables"):
        lines.append("  [PASS    ] Table(s) present - first-row TH headers confirmed")

    if issue_count == 0:
        lines.append("  No issues detected.")

    lines += [
        "",
        "-" * 70,
        "ISSUES FIXED (automated)",
        "-" * 70,
    ]

    all_fixes = list(fixes) + list(alt_log_entries)
    if all_fixes:
        for fix in all_fixes:
            lines.append(f"  * {fix}")
    else:
        lines.append("  (no automated fixes applied)")

    lines += [
        "",
        "-" * 70,
        "MANUAL REVIEW NEEDED",
        "-" * 70,
    ]

    if manual_items:
        for index, item in enumerate(manual_items, start=1):
            lines.append(f"  {index}. {item}")
    else:
        lines.append("  None - document passed all automated checks.")

    lines += ["", "=" * 70]

    return "\n".join(lines)


def write_log(
    log_path: Path,
    *,
    filename: str,
    issues: dict[str, Any],
    fixes: Sequence[str],
    alt_log_entries: Sequence[str],
    manual_items: Sequence[str],
    score: int,
    grade: str,
    county_name: str,
    log_date: str,
) -> None:
    """Write a remediation log to disk."""
    log_path.write_text(
        build_remediation_log(
            filename=filename,
            issues=issues,
            fixes=fixes,
            alt_log_entries=alt_log_entries,
            manual_items=manual_items,
            score=score,
            grade=grade,
            county_name=county_name,
            log_date=log_date,
        ),
        encoding="utf-8",
    )
