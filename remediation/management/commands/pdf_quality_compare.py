"""
Compares two sets of `pdf_quality_report` output: a baseline (the current stack) and a
candidate (the stack being tested). Each argument is one report file, or a folder of
them (files are matched by name).

Prints what got worse, what got better, and the pages that changed so a person can open
them. Not pass/fail by default, since a change may be intended. `--fail-on-regression`
exits non-zero if anything got worse, for a script to act on.
"""

import json
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from remediation.pdf_quality import compare_reports


def _load(path: Path) -> dict[str, dict[str, Any]]:
    if path.is_dir():
        return {p.name: json.loads(p.read_text()) for p in sorted(path.glob("*.json"))}
    if path.is_file():
        return {path.name: json.loads(path.read_text())}
    raise CommandError(f"Not a file or folder: {path}")


class Command(BaseCommand):
    help = "Compares two sets of pdf_quality_report output (baseline vs candidate)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("baseline", type=Path, help="Report file or folder: current stack.")
        parser.add_argument("candidate", type=Path, help="Report file or folder: stack to test.")
        parser.add_argument(
            "--fail-on-regression",
            action="store_true",
            help="Exit non-zero if anything got worse.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        baseline, candidate = _load(options["baseline"]), _load(options["candidate"])
        if options["baseline"].is_file() and options["candidate"].is_file():
            # Two single files are compared whatever they are called.
            baseline, candidate = (
                {"report": next(iter(baseline.values()))},
                {"report": next(iter(candidate.values()))},
            )
        names = sorted(set(baseline) & set(candidate))
        if not names:
            raise CommandError("No report names in common between the two arguments.")
        for name in sorted(set(baseline) ^ set(candidate)):
            self.stdout.write(self.style.WARNING(f"Only on one side, skipped: {name}"))

        regressed = []
        for name in names:
            comparison = compare_reports(baseline[name], candidate[name])
            self.stdout.write(f"\n=== {name} ===")
            for label, before, after in comparison.rows:
                marker = "" if before == after else "  <- changed"
                self.stdout.write(f"  {label:34}{before!s:>8}{after!s:>8}{marker}")
            for title, lines, style in (
                ("Worse", comparison.regressions, self.style.ERROR),
                ("Better", comparison.improvements, self.style.SUCCESS),
            ):
                if lines:
                    self.stdout.write(style(f"{title}:"))
                    self.stdout.write("\n".join(f"  {line}" for line in lines))
            if comparison.page_changes:
                self.stdout.write(f"Pages that changed ({len(comparison.page_changes)}):")
                self.stdout.write("\n".join(f"  {line}" for line in comparison.page_changes))
            if comparison.regressions:
                regressed.append(name)

        if regressed and options["fail_on_regression"]:
            raise CommandError(f"Regressions in: {', '.join(regressed)}")
