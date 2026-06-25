"""OpenDataLoader PDF adapter — auto-tags untagged PDFs into Tagged PDFs.

OpenDataLoader spawns a JVM subprocess per convert() call.  Initialise once
and reuse; each call to tag_pdf() is a fresh JVM launch (unavoidable with the
current Java CLI architecture, but fast — ~10-15s for a 200-page document).

Requires Java 21+ on PATH and opendataloader-pdf installed:
    pip install opendataloader-pdf
    apt-get install openjdk-21-jre-headless
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Protocol


class TaggingAdapter(Protocol):
    def tag_pdf(self, source_pdf: Path, output_dir: Path) -> Path:
        """Auto-tag a PDF and return the path to the tagged output file."""


class OpenDataLoaderAdapter:
    """Production adapter — calls the OpenDataLoader Java CLI via its Python wrapper."""

    def tag_pdf(self, source_pdf: Path, output_dir: Path) -> Path:
        try:
            import opendataloader_pdf
        except ImportError as e:
            raise RuntimeError(
                "opendataloader-pdf is not installed. "
                "Add it to the [remediation] extras and rebuild."
            ) from e

        output_dir.mkdir(parents=True, exist_ok=True)
        opendataloader_pdf.convert(
            input_path=[str(source_pdf)],
            output_dir=str(output_dir),
            format="tagged-pdf",
        )

        # OpenDataLoader appends _tagged to the stem: "budget.pdf" → "budget_tagged.pdf"
        tagged = output_dir / f"{source_pdf.stem}_tagged.pdf"
        if not tagged.exists():
            # Fallback: find any PDF in the output dir
            candidates = list(output_dir.glob("*.pdf"))
            if not candidates:
                raise RuntimeError(
                    f"OpenDataLoader produced no PDF in {output_dir}. "
                    f"Source was: {source_pdf}"
                )
            tagged = candidates[0]

        return tagged


class LocalTaggingStub:
    """No-op adapter for local tests — copies source PDF so the pipeline can run."""

    def tag_pdf(self, source_pdf: Path, output_dir: Path) -> Path:
        import shutil
        output_dir.mkdir(parents=True, exist_ok=True)
        out = output_dir / f"{source_pdf.stem}_tagged.pdf"
        shutil.copy2(source_pdf, out)
        return out
