# Dice Document Pipeline API

## Setup

Setup a python3 env using 3.13 or above and activate it

```bash
pip install -r requirements.txt
pre-commit install
python manage.py migrate
python manage.py runserver
```

Linting and type checking are enforced via Ruff and MyPy and will automatically run when attempting to commit.

## Problem Definition

In the first half of 2026, Jerry Happel and Drake Olejniczak from the [Van Buren County Digital Information Department](https://vanburencountymi.gov/departments/departments-offices/digital-information/) were facing an interesting and pressing problem: the [Van Buren County website](https://vanburencountymi.gov) contained almost 4000 media files, includes thousands of pdf files that would not meet the WCAG 2.1 Level AA technical standard required by ADA Title II web and mobile accessibility compliance.

## V1 Prototype

With the deadline swiftly approaching, they created the [Dice Document Pipeline](https://github.com/vanburencountymi-digital-information/dice-document-pipeline) as a protoype; this AI assisted document remediation pipeline requires a windows desktop, on which it drives Adobe Acrobat Pro through COM automation to OCR, auto-tag, and run Acrobat's accessibility check. Then, another pass applies python remediation fixes, generates Claude-assisted alt test/headings/contrast checks, scores compliance, and writes per-document logs.

### V1 Limitations

The prototype was able to batch remediate over a thousand documents to a high standard, proving AI tools could be an effective solution to the problem of document remediation. However, as a protoype, it had several important limitations:
- Required the usage of a windows desktop with adobe acrobat
- AI tools were most costly than algorithmic solutions
- Would require human intervention at regular intervals to scrape the website for new documents, download them, batch convert them, and upload them, which represented a significant administrative burden.

## V2 Prototype

The planned V2 prototype is an API that leverages a number of new open-source packages with the following pipeline:

- END USER uploads a document to a platform that has a plugin that taps into the remediation pipeline (i.e., WordPress)
- Worker sends call to Django API with file
- Django API creates a remediation job and instantiates a background worker (Celery)
- Worker checks for OCR/text extraction and performs it if necessary with OpenDataLoader
- Worker applies PDF Tags + Metadata with OpenDataLoader and python remediation fixes (check on this to see how much open data loader can do and how much is needed via claude)
- Final document is written (likely claude?)
- Document is validated with VeraPDF
- If pass, return remediated PDF
- If fail, enter secondary AI pipeline, which?

## OLD README FROM V1

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
