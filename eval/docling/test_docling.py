"""
Docling eval harness — extract markdown + HTML from county documents.

Runs fully locally, no API calls or costs after initial model download.
Compare results against eval/results/ (Adobe PDF Services run).

Usage:
    python test_docling.py                   # all PDFs in ../sample-docs/
    python test_docling.py path/to/file.pdf  # single file

Output:
    ../results/docling/<stem>.md             — extracted markdown
    ../results/docling/<stem>.html           — HTML rendering
    ../results/docling/run_summary.json      — per-doc metrics + comparison notes

Scoring dimensions (review manually after run):
    - Headings: are section headings detected and correctly levelled?
    - Tables: are table structures preserved? (critical for county docs)
    - Reading order: does content flow sensibly?
    - HTML quality: would this render acceptably as a Document CPT page?
"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path

SAMPLE_DOCS = Path(__file__).parent.parent / "sample-docs"
RESULTS_DIR = Path(__file__).parent.parent / "results" / "docling"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# Adobe run for side-by-side comparison (keyed by original filename)
ADOBE_RESULTS_PATH = Path(__file__).parent.parent / "results" / "run_summary.json"


def load_adobe_results() -> dict:
    if not ADOBE_RESULTS_PATH.exists():
        return {}
    with open(ADOBE_RESULTS_PATH) as f:
        data = json.load(f)
    return {r["file"]: r for r in data.get("results", [])}


def extract_metrics(result) -> dict:
    """Pull structural metrics from a DoclingDocument."""
    doc = result.document
    metrics = {}

    # Page count
    try:
        metrics["pages"] = len(doc.pages)
    except Exception:
        metrics["pages"] = None

    # Table count
    try:
        metrics["tables"] = len(list(doc.tables))
    except Exception:
        metrics["tables"] = None

    # Figure / picture count
    try:
        metrics["figures"] = len(list(doc.pictures))
    except Exception:
        metrics["figures"] = None

    # Heading count (rough: count lines starting with # in markdown)
    try:
        md = doc.export_to_markdown()
        metrics["headings"] = sum(1 for line in md.splitlines() if line.startswith("#"))
        metrics["word_count"] = len(md.split())
    except Exception:
        metrics["headings"] = None
        metrics["word_count"] = None

    return metrics


def process_pdf(converter, input_path: Path, adobe_results: dict) -> dict:
    print(f"\n-> {input_path.name}")
    start = time.time()

    try:
        result = converter.convert(str(input_path))
    except Exception as e:
        elapsed = round(time.time() - start, 1)
        print(f"   ERROR ({elapsed}s): {e}")
        return {
            "file": input_path.name,
            "status": "error",
            "error": str(e),
            "elapsed_seconds": elapsed,
        }

    elapsed = round(time.time() - start, 1)
    stem = input_path.stem
    metrics = extract_metrics(result)

    # Save markdown
    md_path = RESULTS_DIR / f"{stem}.md"
    try:
        md_content = result.document.export_to_markdown()
        md_path.write_text(md_content, encoding="utf-8")
    except Exception as e:
        md_content = ""
        print(f"   WARN: markdown export failed: {e}")

    # Save HTML
    html_path = RESULTS_DIR / f"{stem}.html"
    try:
        html_content = result.document.export_to_html()
        html_path.write_text(html_content, encoding="utf-8")
    except Exception as e:
        html_content = ""
        print(f"   WARN: HTML export failed: {e}")

    file_size_kb = round(input_path.stat().st_size / 1024, 1)
    adobe = adobe_results.get(input_path.name, {})
    adobe_status = adobe.get("status", "not_run")
    adobe_time = adobe.get("elapsed_seconds")

    speedup = None
    if adobe_time and elapsed:
        speedup = round(adobe_time / elapsed, 1)

    print(f"   {elapsed}s | {metrics.get('pages')} pages | "
          f"{metrics.get('tables')} tables | {metrics.get('headings')} headings | "
          f"{metrics.get('word_count')} words")
    if speedup:
        comparison = "faster" if speedup > 1 else "slower"
        print(f"   vs Adobe: {adobe_time}s -> {abs(speedup)}x {comparison}")

    return {
        "file": input_path.name,
        "size_kb": file_size_kb,
        "status": "ok",
        "elapsed_seconds": elapsed,
        "markdown_path": str(md_path),
        "html_path": str(html_path),
        "metrics": metrics,
        "vs_adobe": {
            "adobe_status": adobe_status,
            "adobe_elapsed_seconds": adobe_time,
            "speedup_factor": speedup,
        },
    }


def main():
    from docling.document_converter import DocumentConverter

    if len(sys.argv) > 1:
        pdf_paths = [Path(p) for p in sys.argv[1:] if Path(p).suffix.lower() == ".pdf"]
    else:
        pdf_paths = sorted(SAMPLE_DOCS.glob("*.pdf"))

    if not pdf_paths:
        print(f"No PDFs found in {SAMPLE_DOCS}. Pass paths as args or drop PDFs there.")
        sys.exit(1)

    print(f"Docling eval — {len(pdf_paths)} document(s)")
    print(f"Results -> {RESULTS_DIR}")
    print("Note: first run downloads ML models (~1-2 GB). Subsequent runs are fast.\n")

    adobe_results = load_adobe_results()
    if adobe_results:
        print(f"Adobe results loaded for comparison ({len(adobe_results)} docs)\n")

    # Initialise converter once — loads models into memory
    # Reduced memory settings: lower image scale, skip page/picture image generation
    # (we care about text + structure extraction, not image rendering)
    print("Loading Docling models...")
    t0 = time.time()
    try:
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import PdfFormatOption
        pipeline_options = PdfPipelineOptions()
        pipeline_options.images_scale = 1.0          # default 2.0 — halves image memory
        pipeline_options.generate_page_images = False
        pipeline_options.generate_picture_images = False
        converter = DocumentConverter(
            format_options={"pdf": PdfFormatOption(pipeline_options=pipeline_options)}
        )
    except (ImportError, TypeError):
        # Fallback if API differs in installed version
        converter = DocumentConverter()
    print(f"Models ready in {round(time.time() - t0, 1)}s\n")

    # Process smallest files first — large docs are more likely to hit memory limits
    pdf_paths = sorted(pdf_paths, key=lambda p: p.stat().st_size)

    summary = []
    for path in pdf_paths:
        result = process_pdf(converter, path, adobe_results)
        summary.append(result)

    ok = [r for r in summary if r.get("status") == "ok"]
    errors = [r for r in summary if r.get("status") != "ok"]
    total_time = round(sum(r.get("elapsed_seconds", 0) for r in summary), 1)

    summary_path = RESULTS_DIR / "run_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "run_at": datetime.utcnow().isoformat(),
                "tool": "docling",
                "results": summary,
            },
            f,
            indent=2,
        )

    print(f"\n{'='*55}")
    print(f"Completed:  {len(ok)}/{len(summary)} documents")
    if errors:
        print(f"Errors:     {[e['file'] for e in errors]}")
    print(f"Total time: {total_time}s")
    print(f"\nNext steps:")
    print(f"  1. Open a few .html files in a browser — would this work as a Document CPT page?")
    print(f"  2. Check .md files — are tables, headings, and reading order correct?")
    print(f"  3. Spot-check the complex docs (minutes, RFP) for table fidelity")
    print(f"  Summary -> {summary_path}")


if __name__ == "__main__":
    main()
