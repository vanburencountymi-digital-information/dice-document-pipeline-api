"""pikepdf post-processing pass applied after OpenDataLoader auto-tagging.

OpenDataLoader produces a structurally tagged PDF but has two bugs:
  - Sets MarkInfo.Marked = False (Adobe and PAC 2024 fail "Document is tagged PDF")
  - Omits /Lang on the document catalog (PAC 2024 fails "Language")

This module also writes the document title and sets tab order to Struct on
every page, fixing two additional PAC 2024 failures.
"""

from __future__ import annotations

from pathlib import Path


def finalize_tagged_pdf(
    tagged_path: Path,
    *,
    title: str,
    lang: str = "en-US",
) -> Path:
    """Apply metadata and flag fixes to a tagged PDF in-place.

    Fixes applied:
      - MarkInfo.Marked = True   (PAC: "Document is tagged PDF")
      - Root.Lang = lang         (PAC: "Language")
      - /Info Title + XMP dc:title = title  (PAC: "Title")
      - /Tabs /S on every page   (PAC: "Tab order")

    Modifies the file in-place and returns the same path.
    """
    import pikepdf

    with pikepdf.open(tagged_path, allow_overwriting_input=True) as pdf:
        pdf.Root.MarkInfo = pikepdf.Dictionary(Marked=True)
        pdf.Root.Lang = lang

        with pdf.open_metadata() as meta:
            meta["dc:title"] = title

        for page in pdf.pages:
            page["/Tabs"] = pikepdf.Name("/S")

        pdf.save()

    return tagged_path
