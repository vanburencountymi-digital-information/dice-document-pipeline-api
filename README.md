# DICE Document Pipeline

ADA PDF remediation and evaluation tooling for the DICE County Knowledge Base Pipeline.

This repository currently contains the desktop batch pipeline that was proven against Van Buren County and St. Joseph County document sets, plus an Adobe PDF Services Auto-Tag evaluation harness. The next phase is to refactor the reusable remediation logic into a server worker for the County Knowledge Base Pipeline.

## Current Components

- `pipeline/acrobat_pass.py` - Windows desktop Pass 1. Drives Adobe Acrobat Pro through COM automation to OCR, auto-tag, and run Acrobat's accessibility check.
- `pipeline/ada_remediate.py` - Pass 2. Applies Python remediation fixes, generates Claude-assisted alt text/headings/contrast checks, scores compliance, and writes per-document logs.
- `pipeline/*.bat` - Windows launchers for installation, Pass 1, Pass 2, reset, and rescore workflows.
- `config/constants.py` - County identity, model, and path defaults.
- `eval/adobe-pdf-services/test_autotag.py` - Adobe PDF Services Auto-Tag evaluation harness for the possible Linux/server path.

Runtime artifacts such as source PDFs, remediated PDFs, logs, status CSVs, and `.env` files are intentionally ignored by Git.

## Requirements

- Python 3.11 or newer
- Windows with Adobe Acrobat Pro for the current desktop COM pipeline
- Poppler for `pdf2image`
- Tesseract OCR for fallback OCR
- Ghostscript for font embedding
- Anthropic API key for Claude-assisted alt text, heading detection, and contrast checks

The current desktop pipeline is Windows-oriented. The Adobe PDF Services eval harness is the starting point for a future Linux-compatible server worker.

## Setup

1. Clone the repo.
2. Copy `.env.example` to `.env`.
3. Set `ANTHROPIC_API_KEY` in `.env` or your shell environment.
4. From `pipeline/`, run `INSTALL_DEPENDENCIES.bat`, or install `pipeline/requirements.txt` into a Python 3.11+ environment.
5. Place source PDFs in `pipeline/remediation_work/downloads/`.
6. Run `pipeline/RUN_PIPELINE.bat`.

For PowerShell, one simple way to load `.env` into the current user environment is:

```powershell
Get-Content .env | ForEach-Object {
  $k, $v = $_ -split '=', 2
  if ($k -and -not $k.StartsWith('#')) {
    [System.Environment]::SetEnvironmentVariable($k, $v)
  }
}
```

## Desktop Pipeline Flow

1. `acrobat_pass.py` scans `remediation_work/downloads/`, creates or updates `remediation_status.csv`, and processes pending PDFs through Acrobat.
2. `ada_remediate.py` processes rows where `pipeline_pass_status = pending`, writes remediated PDFs to `remediation_work/remediated/`, writes logs to `remediation_work/logs/`, and updates scores in the CSV.
3. Reset and rescore helper scripts operate on the local CSV and output folders.

See `docs/desktop-pipeline.md` for operational details.

## Adobe PDF Services Evaluation

The eval harness in `eval/adobe-pdf-services/` tests Adobe PDF Services Auto-Tag against sample PDFs. Auto-Tag pricing must be modeled by page: Adobe currently charges Auto-Tag as document transactions per page, so validate dashboard usage before assuming production cost.

## Next Refactor

The desktop scripts are useful proof, but the server product needs a different shape:

- extract reusable remediation functions from CLI/CSV orchestration
- replace local CSV state with PostgreSQL job and document records
- replace local folders with object storage
- return structured JSON results
- keep audit/cost/version metadata for every external API call

See `docs/server-worker-refactor-plan.md`.
