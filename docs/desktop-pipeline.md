# Desktop ADA Pipeline

The current pipeline is a proven Windows desktop batch workflow. It should be treated as the working legacy implementation until the server worker is built.

## Inputs And Outputs

Input PDFs:

```text
pipeline/remediation_work/downloads/
```

Generated status CSV:

```text
pipeline/remediation_status.csv
```

Remediated PDFs:

```text
pipeline/remediation_work/remediated/
```

Per-document logs:

```text
pipeline/remediation_work/logs/
```

These runtime files are ignored by Git.

## Pass 1: Acrobat Automation

`pipeline/acrobat_pass.py` requires Windows, Adobe Acrobat Pro, and `pywin32`.

It:

- creates or updates `remediation_status.csv`
- detects scanned PDFs
- runs OCR when needed
- runs Acrobat AutoTag
- runs Acrobat's full accessibility check
- marks Pass 1 status in the CSV

This pass is desktop-bound because it drives Acrobat through Windows COM automation.

## Pass 2: Python Remediation

`pipeline/ada_remediate.py` reads the CSV and processes pending rows.

It:

- applies metadata and language fixes
- sets root accessibility flags
- sets tab order
- fixes form field tooltips
- promotes simple first-row table headers
- embeds fonts with Ghostscript when available
- applies Tesseract OCR fallback when needed
- generates Claude-assisted image alt text
- detects headings with Claude Vision or pdfplumber heuristics
- samples pages for color contrast review
- writes a compliance score, grade, and manual review items

## Known Limits

- Acrobat COM automation is not suitable for a Linux server worker.
- CSV state is local and not safe as the platform source of truth.
- The desktop scripts assume local folders rather than object storage.
- The score is an operational triage score, not a legal certification.
- Human review remains necessary for complex tables, legal documents, maps, engineering drawings, vague links, and any politically or legally sensitive records.

## Good Parts To Preserve

- document assessment heuristics
- pikepdf remediation functions
- alt text caching
- structured per-document logs
- score and grade output
- manual review item generation
- Tesseract/Ghostscript fallback behavior

Those should be extracted into testable library functions during the server-worker refactor.
