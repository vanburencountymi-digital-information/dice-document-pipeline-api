"""
Golden-baseline regression report for the opendataloader-hybrid OCR/tagging step.

No test in this repo exercises real OCR/Docling output — the unit tests all mock
`OpenDataLoaderAdapter`/`opendataloader_pdf.convert` directly. This command runs the real
adapter against every real PDF under `remediation/tests/fixtures/original/` and
`remediation/tests/fixtures/edge-cases/`, and prints a diff of a text/metadata/structure
fingerprint against a committed baseline, so a torch/Docling-fork/opendataloader-pdf version
bump that changes real OCR output is visible and reviewable — not just a pass/fail.

Deliberately not pass/fail: an intentional model/library improvement is *expected* to change
output, so a diff here isn't automatically a bug. It's a prompt to look at exactly what
changed and decide, then `--record` to accept it as the new baseline if it's a real
improvement (or investigate further if it looks like a regression). This also covers a fixture
that's *expected* to raise (e.g. a currently-unfixed upstream crash) — the error itself is part
of the fingerprint, so a fix landing (or a new crash appearing) shows up as an ordinary diff
line instead of aborting the whole run.

Deliberately not wired into `make test`/CI either — it needs the real `opendataloader-hybrid`
container running and is meant to be run by hand around a dependency bump (see the
Makefile's `check-ocr-regression` target and README.md's "Dependency Upgrades" section).
"""

import difflib
import json
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import pikepdf
import pymupdf as fitz
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from remediation.adapters.base import AdapterError
from remediation.adapters.ocr.open_data_loader import OpenDataLoaderAdapter

FIXTURES_ROOT = Path(__file__).resolve().parent.parent.parent / "tests/fixtures"
FIXTURE_DIRS = [FIXTURES_ROOT / "original", FIXTURES_ROOT / "edge-cases"]
BASELINE_DIR = FIXTURES_ROOT / "ocr_regression_baseline"


def _discover_fixtures() -> list[Path]:
    """Every real PDF under fixtures/original/ and fixtures/edge-cases/ — not a hardcoded
    shortlist, so a fixture added to either later is covered automatically.
    """
    return sorted(p for d in FIXTURE_DIRS for p in d.glob("*.pdf"))


def _structure_tag_counts(pdf_path: Path) -> dict[str, int]:
    """Coarse fingerprint of a tagged PDF's structure tree: how many of each tag type
    appears, ignoring page-numbering/ordering details that can shift harmlessly.
    """
    counts: Counter[str] = Counter()
    with pikepdf.open(pdf_path) as pdf:
        root = pdf.Root.get("/StructTreeRoot")
        if root is None:
            return {}

        def walk(node: Any) -> None:
            kids = node.get("/K")
            if kids is None:
                return
            if not isinstance(kids, pikepdf.Array):
                kids = [kids]
            for kid in kids:
                if isinstance(kid, pikepdf.Dictionary):
                    tag = kid.get("/S")
                    if tag is not None:
                        counts[str(tag)] += 1
                    walk(kid)

        walk(root)
    return dict(sorted(counts.items()))


def _fingerprint(pdf_path: Path) -> dict[str, Any]:
    with fitz.open(pdf_path) as doc:
        # Lines, not one big string per page — json.dumps(indent=2) then lays each line out
        # on its own row, so the committed baseline file is actually readable/diffable by a
        # human (git diff, a code review) instead of one giant \n-escaped string per page.
        page_text = [doc[i].get_text().strip().splitlines() for i in range(doc.page_count)]
        metadata = dict(doc.metadata)
    return {
        "extraction_error": None,
        "page_count": len(page_text),
        "page_text": page_text,
        "metadata": metadata,
        "structure_tag_counts": _structure_tag_counts(pdf_path),
    }


def _build_fingerprint(adapter: OpenDataLoaderAdapter, fixture_path: Path) -> dict[str, Any]:
    """Runs the real adapter and fingerprints its output — or, if it raises (e.g. a
    known/still-unfixed upstream crash on one of the edge-case fixtures), records that as
    the fingerprint instead of letting it abort the whole command. A change in whether/how
    a fixture errors is itself a real, reportable signal (the crash was fixed, or a new one
    appeared) — not a special case to hide.
    """
    with TemporaryDirectory() as output_dir:
        try:
            output_path = Path(adapter.extract(str(fixture_path), output_dir=output_dir))
        except AdapterError as exc:
            return {
                "extraction_error": str(exc),
                "page_count": None,
                "page_text": None,
                "metadata": None,
                "structure_tag_counts": None,
            }
        return _fingerprint(output_path)


class Command(BaseCommand):
    """Diffs real opendataloader-hybrid OCR/tagging output (text, PDF metadata,
    structure-tag counts, or an extraction error) against a committed baseline — a
    presaved-output regression *report* for major dependency bumps (torch, the Docling
    fork, opendataloader-pdf), not a pass/fail gate. Not run on every PR.
    """

    help = (
        "Diffs real opendataloader-hybrid OCR/tagging output against a committed baseline. "
        "Not pass/fail — prints what changed so you can judge whether it's expected. Use "
        "--record to accept the current output as the new baseline."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--record",
            action="store_true",
            help="(Re)write the baseline file(s) from the current output instead of diffing "
            "against them. Do this after reviewing a diff and deciding the change is "
            "expected — not just to make a diff go away.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        record = options["record"]
        adapter = OpenDataLoaderAdapter(hybrid_url=settings.OPENDATALOADER_HYBRID_URL)

        fixtures = _discover_fixtures()
        if not fixtures:
            raise CommandError(f"No PDF fixtures found under {FIXTURE_DIRS}")

        any_diff = False
        for fixture_path in fixtures:
            label = str(fixture_path.relative_to(FIXTURES_ROOT))
            fingerprint = _build_fingerprint(adapter, fixture_path)

            baseline_path = (BASELINE_DIR / fixture_path.relative_to(FIXTURES_ROOT)).with_suffix(
                ".json"
            )

            if record:
                baseline_path.parent.mkdir(parents=True, exist_ok=True)
                baseline_path.write_text(json.dumps(fingerprint, indent=2, sort_keys=True) + "\n")
                self.stdout.write(self.style.SUCCESS(f"Recorded baseline: {baseline_path}"))
                continue

            if not baseline_path.exists():
                raise CommandError(
                    f"No baseline at {baseline_path} — run with --record first to create one."
                )
            baseline = json.loads(baseline_path.read_text())

            self.stdout.write(f"\n=== {label} ===")
            file_changed = self._report_diff(baseline, fingerprint)
            any_diff = any_diff or file_changed

        if record:
            return

        if any_diff:
            self.stdout.write(
                self.style.WARNING(
                    "\nOutput differs from the recorded baseline (see diff above). This is "
                    "not automatically a problem — if the change is expected (e.g. a real "
                    "model/library improvement, or an upstream crash getting fixed), review "
                    "it, then re-run with --record to accept it as the new baseline. If it "
                    "looks unintended, investigate before merging."
                )
            )
        else:
            self.stdout.write(self.style.SUCCESS("\nNo change from the recorded baseline."))

    def _report_diff(self, baseline: dict[str, Any], current: dict[str, Any]) -> bool:
        changed = False

        if baseline.get("extraction_error") != current.get("extraction_error"):
            self.stdout.write(
                f"  extraction_error: {baseline.get('extraction_error')!r} -> "
                f"{current.get('extraction_error')!r}"
            )
            changed = True

        if baseline.get("extraction_error") or current.get("extraction_error"):
            # Nothing else to compare when either side failed to extract at all.
            if not changed:
                self.stdout.write(self.style.SUCCESS("  no change"))
            return changed

        if baseline["page_count"] != current["page_count"]:
            self.stdout.write(f"  page_count: {baseline['page_count']} -> {current['page_count']}")
            changed = True

        if self._report_dict_diff("metadata", baseline["metadata"], current["metadata"]):
            changed = True

        if self._report_dict_diff(
            "structure_tag_counts",
            baseline["structure_tag_counts"],
            current["structure_tag_counts"],
        ):
            changed = True

        for i, (old_page, new_page) in enumerate(
            zip(baseline["page_text"], current["page_text"], strict=False)
        ):
            if old_page == new_page:
                continue
            changed = True
            self.stdout.write(f"  --- page {i} text diff (baseline vs. current) ---")
            diff = difflib.unified_diff(
                old_page,
                new_page,
                fromfile="baseline",
                tofile="current",
                lineterm="",
            )
            for line in diff:
                self.stdout.write(f"  {line}")

        if not changed:
            self.stdout.write(self.style.SUCCESS("  no change"))
        return changed

    def _report_dict_diff(self, label: str, old: dict[str, Any], new: dict[str, Any]) -> bool:
        """Prints an added/removed/changed report for a flat dict. Returns whether anything differs."""
        changed = False
        for key in sorted(set(old) | set(new)):
            old_value = old.get(key)
            new_value = new.get(key)
            if old_value == new_value:
                continue
            changed = True
            if key not in old:
                self.stdout.write(f"  {label}.{key}: (new) -> {new_value!r}")
            elif key not in new:
                self.stdout.write(f"  {label}.{key}: {old_value!r} -> (removed)")
            else:
                self.stdout.write(f"  {label}.{key}: {old_value!r} -> {new_value!r}")
        return changed
