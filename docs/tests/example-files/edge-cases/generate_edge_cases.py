"""Regenerates the OCR-fallback edge-case fixtures in this directory from their source document.

Context: a real 47-page submission ("0. 08-18-26 BOC Meeting Packet.pdf" — a compiled packet of
unrelated documents: meeting minutes, a medical CV, a vendor quote) crashed the whole remediation
pipeline with a NullPointerException during auto-tagging. 14 pages were flagged during triage as
needing OCR fallback with "0 OCR words available" — most of those are benign (signature images,
webpage screenshots with genuinely no recoverable text), but one pair (pages 12-13) triggers a
real bug in opendataloader-pdf's hybrid OCR-fallback font handling (see
dice-document-pipeline-api's implementation_plan.md and
~/.claude/plans/fully-investigate-this-bug-woolly-stardust.md for the full root-cause writeup).

Each fixture below is a cluster of adjacent flagged pages, bracketed with a 1-page buffer on
either side. The buffer matters: isolating a single flagged page (even forced through
`--hybrid-mode full`) does NOT reproduce this bug class — the underlying triage/OCR-recall
behavior is sensitive to document-wide context, so a fixture needs enough surrounding pages to
preserve the same behavior the page had inside the real 47-page document. Confirmed empirically
this session by testing single-page isolation vs. multi-page slices against the real bug.

Usage: run from the repo root with the project's .venv (needs `pikepdf`):
    .venv/bin/python3 docs/tests/example-files/edge-cases/generate_edge_cases.py
"""

import pikepdf

# Points at whatever local copy of the source document is currently on disk — the original
# remediation-scoped path gets cleared whenever remediations are reset, so this isn't stable;
# re-point it at wherever you've re-uploaded the file before regenerating.
SOURCE_PDF = "media/0. 08-18-26 BOC Meeting Packet.pdf"

# (first_page, last_page, verdict) — 1-indexed, inclusive. "verdict" is baked into the filename
# so you know what to expect without opening the fixture. Filled in from this session's live
# testing against the real opendataloader-pdf CLI (--hybrid docling-fast).
#
# All 9 originally-flagged clusters were tested live and visually inspected — only [12,13]
# reproduces the bug. The other 8 fell into just 3 real content categories, not 8 distinct cases:
#   - signature images (pages 8, 44)
#   - scanned letters with a seal/crest graphic (page 18, and pages 45-46)
#   - dense checkbox-grid retirement-plan forms (pages 20, 23, 25, 27, 28, 33, 34 — five separate
#     clusters, all the SAME two MERS forms, different pages of the same document)
# Kept one representative fixture per category (plus a second checkbox-form one that ends in a
# blank/unsigned signature block, a meaningfully different sub-case) instead of all 8 — the
# dropped ones added no additional coverage, just repeats of an already-represented pattern.
CLUSTERS = [
    (7, 9, "benign"),  # [8] alone — scanned signature image, correctly has nothing to OCR
    (11, 14, "crash"),  # [12,13] — THE bug: CV page + browser-screenshot page together
    (17, 19, "benign"),  # [18] alone — scanned state license letter with a seal graphic
    (26, 29, "benign"),  # [27,28] — MERS checkbox-grid forms (densest example)
    (32, 35, "benign"),  # [33,34] — same MERS form pattern, ends in a blank/unsigned signature
    # Dropped as redundant with the two MERS rows above (same document, same checkbox-form
    # pattern, no new coverage): (19, 21, "benign") [20], (22, 24, "benign") [23],
    # (24, 26, "benign") [25].
    # Dropped as redundant with pages7-9 (signature) + pages17-19 (seal graphic) combined:
    # (43, 47, "benign") [44,45,46].
]

# Single, non-adjacent pages for the font-ToUnicode-offset defect (a separate bug from the
# OCR-fallback crash above — see implementation_plan.md / the font-repair plan). Unlike the
# clusters above, these are single-page fixtures with NO buffer needed: the defect is a static
# property of the embedded font object itself (no cmap table, no /ToUnicode, a constant
# character-code-to-Unicode offset), not sensitive to document-wide triage context the way the
# OCR-fallback bug was. Each entry is (page, filename_suffix) — verified via veraPDF directly
# against the extracted single-page fixture, not just inferred from the full document.
FONT_OFFSET_PAGES = [
    (9, "broken-tounicode-offset29"),  # LONENE+Arial-BoldMT, offset +29 (verified: "DEPARTMENT")
    (
        29,
        "broken-tounicode-offsetneg1",
    ),  # CANKNL+HelveticaNeueLTStd-Lt, offset -1 (verified: "EXCLUDING...SHERIFF")
]


def main() -> None:
    source = pikepdf.open(SOURCE_PDF)
    for first_page, last_page, verdict in CLUSTERS:
        out = pikepdf.Pdf.new()
        for page_index in range(first_page - 1, min(last_page, len(source.pages))):
            out.pages.append(source.pages[page_index])
        filename = f"pages{first_page}-{last_page}-{verdict}-ocr-fallback.pdf"
        out.save(filename)
        print(f"{filename}  ({len(out.pages)} pages)")

    for page, suffix in FONT_OFFSET_PAGES:
        out = pikepdf.Pdf.new()
        out.pages.append(source.pages[page - 1])
        filename = f"page{page}-{suffix}.pdf"
        out.save(filename)
        print(f"{filename}  (1 page)")


if __name__ == "__main__":
    main()
