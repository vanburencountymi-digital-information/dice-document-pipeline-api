"""
Adobe PDF Services Auto-Tag API — eval harness

Usage:
    python test_autotag.py                  # process all PDFs in ../sample-docs/
    python test_autotag.py path/to/file.pdf # process a single file

Credentials:
    Set PDF_SERVICES_CLIENT_ID and PDF_SERVICES_CLIENT_SECRET in a .env file
    in this directory (copy .env.example), or export them as environment vars.

Output:
    ../results/<filename>_tagged.pdf    — accessibility-tagged PDF
    ../results/<filename>_report.xlsx   — tagging report (optional)
    ../results/run_summary.json         — transaction count + timing per doc
"""

import os
import sys
import json
import time
import glob
from pathlib import Path
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

from adobe.pdfservices.operation.auth.service_principal_credentials import ServicePrincipalCredentials
from adobe.pdfservices.operation.exception.exceptions import ServiceApiException, ServiceUsageException, SdkException
from adobe.pdfservices.operation.pdf_services import PDFServices
from adobe.pdfservices.operation.pdf_services_media_type import PDFServicesMediaType
from adobe.pdfservices.operation.pdfjobs.jobs.autotag_pdf_job import AutotagPDFJob
from adobe.pdfservices.operation.pdfjobs.params.autotag_pdf.autotag_pdf_params import AutotagPDFParams
from adobe.pdfservices.operation.pdfjobs.result.autotag_pdf_result import AutotagPDFResult

SAMPLE_DOCS = Path(__file__).parent.parent / "sample-docs"
RESULTS_DIR = Path(__file__).parent.parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)


def get_credentials():
    client_id = os.getenv("PDF_SERVICES_CLIENT_ID")
    client_secret = os.getenv("PDF_SERVICES_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise EnvironmentError(
            "Missing PDF_SERVICES_CLIENT_ID or PDF_SERVICES_CLIENT_SECRET. "
            "Create a .env file from .env.example."
        )
    return ServicePrincipalCredentials(client_id=client_id, client_secret=client_secret)


def autotag_pdf(pdf_services: PDFServices, input_path: Path) -> dict:
    """Run auto-tag on a single PDF. Returns a result summary dict."""
    start = time.time()
    print(f"\n→ {input_path.name}")

    with open(input_path, "rb") as f:
        input_stream = f.read()

    input_asset = pdf_services.upload(
        input_stream=input_stream,
        mime_type=PDFServicesMediaType.PDF,
    )

    params = AutotagPDFParams(
        generate_report=True,   # produces an xlsx report detailing tags added
        shift_headings=True,    # normalises heading hierarchy
    )

    job = AutotagPDFJob(input_asset=input_asset, autotag_pdf_params=params)
    location = pdf_services.submit(job)
    result: AutotagPDFResult = pdf_services.get_job_result(location, AutotagPDFResult)

    stem = input_path.stem
    tagged_path = RESULTS_DIR / f"{stem}_tagged.pdf"
    report_path = RESULTS_DIR / f"{stem}_report.xlsx"

    tagged_asset = result.get_result().get_tagged_pdf()
    tagged_stream = pdf_services.get_content(tagged_asset)
    with open(tagged_path, "wb") as f:
        f.write(tagged_stream.get_input_stream())

    report_asset = result.get_result().get_report()
    if report_asset:
        report_stream = pdf_services.get_content(report_asset)
        with open(report_path, "wb") as f:
            f.write(report_stream.get_input_stream())

    elapsed = round(time.time() - start, 1)
    file_size_kb = round(input_path.stat().st_size / 1024, 1)
    print(f"   done in {elapsed}s  ({file_size_kb} KB input)")
    print(f"   tagged PDF → {tagged_path.name}")

    return {
        "file": input_path.name,
        "size_kb": file_size_kb,
        "elapsed_seconds": elapsed,
        "tagged_pdf": str(tagged_path),
        "report": str(report_path),
        "status": "ok",
    }


def main():
    if len(sys.argv) > 1:
        pdf_paths = [Path(p) for p in sys.argv[1:]]
    else:
        pdf_paths = sorted(SAMPLE_DOCS.glob("*.pdf"))

    if not pdf_paths:
        print(f"No PDFs found. Drop files into {SAMPLE_DOCS} or pass paths as args.")
        sys.exit(1)

    print(f"Adobe PDF Services Auto-Tag eval — {len(pdf_paths)} document(s)")
    print(f"Results → {RESULTS_DIR}\n")

    credentials = get_credentials()
    pdf_services = PDFServices(credentials=credentials)

    summary = []
    for path in pdf_paths:
        try:
            result = autotag_pdf(pdf_services, path)
        except (ServiceApiException, ServiceUsageException, SdkException) as e:
            print(f"   ERROR: {e}")
            result = {"file": path.name, "status": "error", "error": str(e)}
        summary.append(result)

    summary_path = RESULTS_DIR / "run_summary.json"
    with open(summary_path, "w") as f:
        json.dump({"run_at": datetime.utcnow().isoformat(), "results": summary}, f, indent=2)

    ok = [r for r in summary if r.get("status") == "ok"]
    errors = [r for r in summary if r.get("status") != "ok"]
    total_time = sum(r.get("elapsed_seconds", 0) for r in ok)

    print(f"\n{'='*50}")
    print(f"Completed: {len(ok)}/{len(summary)} documents")
    if errors:
        print(f"Errors:    {[e['file'] for e in errors]}")
    print(f"Total time: {total_time}s")
    print(f"Summary → {summary_path}")
    print(
        "\nNOTE: Check your Adobe PDF Services dashboard for actual transaction "
        "consumption — 10 transactions/page for auto-tag. Compare against free "
        "tier (500/month) to extrapolate monthly cost at production volume."
    )


if __name__ == "__main__":
    main()
