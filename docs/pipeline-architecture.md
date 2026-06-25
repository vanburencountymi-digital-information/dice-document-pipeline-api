# Pipeline Architecture — Desktop vs. Server

This document captures the architectural decision made on 2026-06-25 that defines
what the server worker is responsible for and how it differs from the original
desktop pipeline.

## Primary Deliverable: HTML, Not PDF

The original desktop pipeline treated the **remediated PDF** as the primary
deliverable — a compliant PDF that could be posted to the county website.

The server pipeline inverts this priority:

- **Primary deliverable:** Structured HTML and markdown derived by Docling, ingested
  into WordPress as a Document CPT. This is what citizens see and what screen readers
  consume. Docling's layout-aware extraction produces semantically richer output than
  a pikepdf-remediated PDF ever could.
- **Secondary deliverable:** The source PDF, stored in GCS and linked from the WP CPT
  for download. PDF-level ADA compliance is still scored and logged, but a low score
  does not block publishing — it goes to a manual review queue.

This means: **if Docling can extract good HTML from a PDF, the citizen-facing
accessibility requirement is met**, regardless of whether the PDF itself passes
PAC or veraPDF checks.

## What Docling Handles

Docling (IBM Research, open source) runs the full document intelligence pipeline:

| Input condition | What Docling does |
|---|---|
| Born-digital PDF (has text layer) | Layout analysis → structured HTML/markdown |
| Image-only / scanned PDF | **OCR first** (EasyOCR on GPU, Tesseract fallback), then layout analysis → HTML/markdown |
| Mixed PDF (some pages scanned) | Per-page OCR where needed |
| Tables | Extracts table structure into HTML `<table>` elements |
| Headings | Detects heading hierarchy from visual layout |
| Figures | Identifies figure regions (alt text is a separate step, not yet ported) |

**Critical setting:** `pipeline_options.do_ocr = True` must be set on the
`PdfPipelineOptions` object. Without it, Docling skips OCR even on image-only PDFs
and produces empty output. This was missing from the initial server worker
implementation and was added on 2026-06-25.

## What the Server Worker Does (Current State)

```
POST /jobs  ←  PDF upload + metadata
    │
    ▼
GCS inbound bucket  (staged upload)
    │
    ▼
DoclingPdfAdapter.convert_pdf()
    ├── OCR if needed (EasyOCR/GPU → Tesseract fallback)
    ├── Layout analysis (tables, headings, figures)
    ├── HTML export  → GCS artifacts bucket
    └── Markdown export → GCS artifacts bucket
    │
    ▼
process_remediation_job()  [assessment pass]
    ├── assess_document()  — static structural analysis
    ├── score_document()   — compliance score 0–100 + grade
    └── write_log()        — per-document text log → GCS
    │
    ▼
RemediationResult
    ├── html_uri
    ├── markdown_uri
    ├── compliance_score / compliance_grade
    ├── page_count
    ├── processing_seconds  (for cost estimation)
    └── external_api_calls  (for cost estimation)
```

## What the Desktop Pipeline Did (Pass 1 + Pass 2)

The desktop pipeline was a two-pass Windows-only process:

### Pass 1 — acrobat_pass.py (Windows COM, not portable)
- Detect scanned PDFs
- Acrobat OCR (300 DPI) for scanned documents
- Acrobat AutoTag — builds a structure tree with real MCID content mappings
- Acrobat EmbedAllFonts
- Acrobat Full Accessibility Check

### Pass 2 — ada_remediate.py
- Ghostscript font embedding
- pikepdf structural fixes:
  - XMP metadata and DocInfo (title, language, description, producer)
  - `/Lang`, `MarkInfo/Marked`, `ViewerPreferences/DisplayDocTitle`
  - Structure tree injection (if not already tagged by Acrobat)
  - Tab order (`/Tabs /S` on all pages)
  - Form tooltip repair
  - Table header promotion (TD → TH with `/Scope /Column`)
  - Outline bookmark generation from headings
- Tesseract OCR fallback (if Acrobat OCR failed or was skipped)
- **Claude Haiku Vision** — alt text for every substantive image
- **Claude Haiku Vision** — heading detection per page (H1/H2/H3)
- **Claude Haiku Vision** — color contrast check (3-page sample)
- Compliance scoring and log

## What the Server Worker Does NOT Do (by design)

The server worker intentionally omits the PDF-level remediation steps from Pass 2:

- No Ghostscript font embedding
- No pikepdf structure tree injection or structural fixes
- No Claude Vision alt text, heading detection, or contrast checking on the PDF

**Rationale:** These steps improved PDF-level PAC/veraPDF scores but did not affect
the citizen-facing HTML output. The engineering investment needed to port and maintain
them in a server context is not justified when Docling already produces accessible HTML
directly from the source document.

If PDF-level compliance becomes a hard requirement (e.g. for a specific grant, legal
mandate, or VPAT certification), the pikepdf remediation pass from `ada_remediate.py`
can be added as an optional step after the Docling conversion. The code exists and is
Linux-compatible — it just needs to be wired in.

## Acrobat → Docling OCR Quality

The desktop pipeline used Acrobat Pro's OCR engine, which is among the best available
for scanned government documents. Docling uses EasyOCR (GPU-accelerated) which is
strong on modern Latin-script documents. Expected accuracy for typical county finance
documents (budget summaries, audit reports, meeting minutes):

- Born-digital PDFs: identical output quality (no OCR involved)
- Clean scans (modern office scanner, 300+ DPI): EasyOCR quality is comparable
- Old or degraded scans: Acrobat may outperform EasyOCR — flag for manual review

The Finance document test batch (DIC-561) will provide empirical data on this.

## GPU Requirement

Docling's layout model and EasyOCR run efficiently on GPU. Without a GPU:
- OCR on a multi-page scanned document can take 30–120 seconds per page on CPU
- With an NVIDIA L4 (Cloud Run GPU): typically 2–8 seconds per page

This is why the server worker targets Cloud Run GPU (`--gpu=1 --gpu-type=nvidia-l4`)
rather than a standard CPU-only container. The current developer laptop lacks a GPU,
making local Docling testing impractical — Cloud Run is the intended runtime.
