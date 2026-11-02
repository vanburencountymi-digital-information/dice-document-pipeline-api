"""
Writes a repeatable quality report (JSON) for each PDF given. See `remediation/pdf_quality.py`
for what is measured and `pdf_quality_compare` for comparing two sets of reports.

Two ways to use it:

- `--run-pipeline`: each PDF is an original. It is pushed through the real OCR/tagging,
  font-repair, metadata and link steps (never alt text or scoring, so no API key and no
  cost), veraPDF is run after every step, and the final PDF is reported on, with the
  original as the reference for lost and repeated text. Needs the opendataloader-hybrid
  container (`OPENDATALOADER_HYBRID_URL`). Uses the files only, never the database.
- Without it: each PDF is an already-remediated output, reported on as it is.

Like `check_ocr_regression` it is meant to be run by hand around a dependency bump, not in CI.
"""

import json
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from remediation.adapters.base import AdapterError
from remediation.adapters.font_repair.pike_pdf import PikePdfAdapter as FontRepairAdapter
from remediation.adapters.link.pike_pdf import PikePdfAdapter as LinkAdapter
from remediation.adapters.metadata.pike_pdf import PikePdfAdapter as MetadataAdapter
from remediation.adapters.ocr.open_data_loader import OpenDataLoaderAdapter
from remediation.adapters.verification.vera_pdf import VeraPDFAdapter
from remediation.pdf_quality import analyze_pdf


def _failed_checks(verapdf: VeraPDFAdapter, pdf_path: Path) -> dict[str, int]:
    outcome = verapdf.validate(str(pdf_path))
    return {
        f"{rule.clause}/{rule.test_number}": rule.failed_checks for rule in outcome.failed_rules
    }


def _run_stages(
    source: Path, work_dir: Path, *, use_verapdf: bool
) -> tuple[Path, dict[str, Any], dict[str, int] | None]:
    """Runs the four steps in order. A step that raises is recorded and skipped, and the
    next step carries on with the last good PDF, so one failure still shows the rest.
    """
    verapdf = VeraPDFAdapter()
    steps = {
        "ocr": lambda path, out: OpenDataLoaderAdapter(
            hybrid_url=settings.OPENDATALOADER_HYBRID_URL
        ).extract(str(path), output_dir=out),
        "font_repair": lambda path, out: FontRepairAdapter().repair(str(path), output_dir=out),
        "metadata": lambda path, out: MetadataAdapter().finalize(
            str(path), output_dir=out, title=source.stem, lang=settings.LANGUAGE_CODE
        ),
        "link_tag": lambda path, out: LinkAdapter().repair(str(path), output_dir=out),
    }
    stages: dict[str, Any] = {}
    if use_verapdf:
        stages["source"] = {"verapdf": _failed_checks(verapdf, source)}

    current, final_verapdf = source, None
    for name, run in steps.items():
        out = str(work_dir / name)
        started = time.monotonic()
        try:
            current = Path(run(current, out))
        except AdapterError as exc:
            stages[name] = {"error": str(exc)[:300]}
            continue
        stages[name] = {"seconds": round(time.monotonic() - started, 1)}
        if use_verapdf:
            final_verapdf = _failed_checks(verapdf, current)
            stages[name]["verapdf"] = final_verapdf
    return current, stages, final_verapdf


def _truth_for(pdf: Path, truth_dir: Path | None) -> Path | None:
    if truth_dir is None:
        return None
    path = truth_dir / f"{pdf.stem}.json"
    return path if path.is_file() else None


class Command(BaseCommand):
    help = "Writes a JSON quality report per PDF (headings, lists, tables, figures, text, veraPDF)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("pdfs", nargs="+", type=Path, help="PDF files to report on.")
        parser.add_argument(
            "--output-dir", type=Path, required=True, help="Where to write <name>.json files."
        )
        parser.add_argument(
            "--run-pipeline",
            action="store_true",
            help="Treat the PDFs as originals and run them through the pipeline first.",
        )
        parser.add_argument(
            "--source-dir",
            type=Path,
            help="Without --run-pipeline: folder of the originals, matched by file name, to "
            "measure lost and repeated text against.",
        )
        parser.add_argument(
            "--truth-dir",
            type=Path,
            help="Folder of transcriptions (<name>.json) to score the text against, for the "
            "PDFs that have one. See remediation/ocr_truth.py.",
        )
        parser.add_argument("--no-verapdf", action="store_true", help="Skip veraPDF.")
        parser.add_argument(
            "--keep-stages",
            action="store_true",
            help="With --run-pipeline: keep each step's PDF under <output-dir>/<name>-stages/.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        output_dir: Path = options["output_dir"]
        output_dir.mkdir(parents=True, exist_ok=True)
        use_verapdf = not options["no_verapdf"]

        for pdf in options["pdfs"]:
            if not pdf.is_file():
                raise CommandError(f"Not a file: {pdf}")
            self.stdout.write(f"{pdf.name} ...")
            truth = _truth_for(pdf, options["truth_dir"])
            if options["run_pipeline"]:
                report = self._report_from_pipeline(
                    pdf, output_dir, use_verapdf, options["keep_stages"], truth
                )
            else:
                report = self._report_from_output(pdf, options["source_dir"], use_verapdf, truth)
            target = output_dir / f"{pdf.stem}.json"
            target.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
            self.stdout.write(self.style.SUCCESS(f"  wrote {target}"))

    def _report_from_pipeline(
        self,
        pdf: Path,
        output_dir: Path,
        use_verapdf: bool,
        keep_stages: bool,
        truth: Path | None,
    ) -> dict[str, Any]:
        with TemporaryDirectory() as temp:
            work_dir = output_dir / f"{pdf.stem}-stages" if keep_stages else Path(temp)
            work_dir.mkdir(parents=True, exist_ok=True)
            final, stages, final_verapdf = _run_stages(pdf, work_dir, use_verapdf=use_verapdf)
            if final == pdf:
                raise CommandError(f"Every pipeline step failed for {pdf.name}: {stages}")
            report = analyze_pdf(final, source_path=pdf, truth_path=truth)
        report["file"] = pdf.name
        report["stages"] = stages
        # A failed step is skipped, so the numbers describe a PDF that missed that step.
        report["failed_steps"] = [name for name, stage in stages.items() if "error" in stage]
        report["verapdf"] = final_verapdf
        return report

    def _report_from_output(
        self, pdf: Path, source_dir: Path | None, use_verapdf: bool, truth: Path | None
    ) -> dict[str, Any]:
        source = source_dir / pdf.name if source_dir else None
        if source is not None and not source.is_file():
            raise CommandError(f"No original named {pdf.name} in {source_dir}")
        report = analyze_pdf(pdf, source_path=source, truth_path=truth)
        if use_verapdf:
            report["verapdf"] = _failed_checks(VeraPDFAdapter(), pdf)
        return report
