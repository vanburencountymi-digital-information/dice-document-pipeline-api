# Current State Findings — dice-document-pipeline-api

Written 2026-07-28 while investigating whether to start the API. The README's "Next Refactor" section (and the separately-tracked `implementation_plan.md` / Plan 3 in `website-checker`) describe the server-worker refactor as largely *unstarted*, with the auto-tagging engine choice flagged as the single biggest open risk. That's out of date — real work has already happened in this repo and should be reflected in project planning.

## What's already built

Per `git log`, in rough chronological order:

- `b04a213` Implement local remediation worker boundary
- `10662ea` Add Adobe PDF Services SDK adapter (evaluated, not the chosen path — see below)
- `cfcc7cd` Add Docling eval harness — bake-off vs Adobe PDF Services
- `2136e6d` **Docling adapter replaces Adobe in server worker (DIC-156)**
- `99af5bb` GCS storage adapter (DIC-156)
- `f92149d` FastAPI application — upload, status, and health endpoints (DIC-154)
- `e5e9d55` Dockerfile readiness, Docling OCR turned on, SSE progress streaming added
- `861cdf5` / `e6b2e8d` **OpenDataLoader tagging + pikepdf fixes — "two-product PDF pipeline" (DIC-563/564/565/566)**
- `f391e3d` (latest) Docling model baking fix for the Docker build

## Answering "move off Acrobat Pro for desktop" (Maria's stated goal)

This already exists in the code, not just as a plan:

- **OCR/extraction**: `workers/docling_pdf.py` — `DoclingPdfAdapter` uses the open-source Docling library instead of driving Adobe Acrobat via Windows COM automation. Adobe's PDF Services SDK was evaluated (`workers/adobe_pdf_services.py`, `eval/adobe-pdf-services/`) but is not the adapter wired into the server worker.
- **Auto-tagging**: `workers/opendataloader_adapter.py` — `OpenDataLoaderAdapter` calls the OpenDataLoader Java CLI (via its Python wrapper) to auto-tag PDFs into Tagged PDFs. Requires Java 21+ on PATH and the `opendataloader-pdf` Python package. This is the real engine; there's also a `LocalTaggingStub` (no-op copy) used for local tests without Java installed.

Both adapters are selected via `Settings` env flags (`USE_DOCLING`, `USE_OPENDATALOADER`) in `api/config.py`, defaulting to `False` (stub mode) for zero-dependency local dev.

**Practical implication**: Plan 3 (Document Remediation Pipeline v2) in `website-checker`'s plan file describes "auto-tagging engine choice" as the biggest unresolved risk needing a research spike. That spike has effectively already happened here — Docling + OpenDataLoader is the implemented choice, not just a candidate. Plan 3 should be updated to reflect this: the remaining work is closer to *validating and productionizing* the existing choice (accuracy testing, cost/scale estimation, deployment) than *choosing* an engine from scratch.

## What "starting the API" means, concretely

`src/dice_document_pipeline/api/app.py` is a real FastAPI app:

- `POST /jobs` — upload a PDF (multipart), creates a background remediation job, returns a `job_id`.
- `GET /jobs/{job_id}` — poll job status/result.
- `GET /jobs/{job_id}/events` — Server-Sent Events stream of live progress (`queued` → `running` → `complete`/`manual_review`/`failed`).
- `GET /health` — health check.

Two ways to run it, very different in what they actually prove:

1. **Stub mode** (`USE_DOCLING=false`, `USE_OPENDATALOADER=false` — the defaults): `pip install -e ".[api]"` then `uvicorn dice_document_pipeline.api.app:app --reload`. No system dependencies beyond Python. Verifies the API plumbing (upload → job tracking → SSE progress → result) works end-to-end, but `LocalTaggingStub`/`LocalDoclingStub` just copy the source file through — **no real remediation happens**, so this doesn't validate quality.
2. **Real mode**: needs `openjdk-21-jre-headless`, `poppler-utils`, `tesseract-ocr`, `ghostscript` system packages, plus `pip install -e ".[api,docling,gcs,remediation]"` (pulls in PyTorch + Docling's transformer models — sizable download, CPU-only without a GPU). This is what the Dockerfile builds (CUDA-based image, ~3GB of baked models, meant for Cloud Run). None of this is installed on this dev machine yet.

Checked locally: no `java`, no `docling`, no `opendataloader_pdf` currently available — real mode isn't runnable here without a real install step first (deferred, not yet decided how/where to do this — see Open Questions).

## How this connects to other in-flight work

- **Plan 2** (`website-checker` plan file) — the 30-document V1-original/V2-remediated benchmark sample being built into `test_documents/{unremediated,remediated}/` in *this* repo exists specifically so this pipeline's real-mode output can be compared against the old Adobe/Jerry process, document-by-document. That comparison only becomes possible once real mode is actually running somewhere (locally or via Docker).
- **Plan 3 Workstream B** (`website-checker` plan file) — needs updating to reflect that the engine choice (Docling + OpenDataLoader) is already made and implemented, not still open. The remaining real risks are more around validation (does it actually produce compliant output at the quality of the old process?) and productionization (Postgres job state instead of the current in-memory `InMemoryJobStore`, real deployment, cost at scale) than engine selection.

## Suggested design change: dual OCR/tagging paths with fallback

Maria's idea, worth building in rather than treating Docling as the sole path: run **both** a Docling path and an OpenDataLoader path for OCR/tagging, and fall back from one to the other when the primary result's PDF/UA verification (veraPDF check) comes back low/failing. Currently `app.py`'s adapter factories (`_build_docling()`, `_build_tagging()`) pick a single adapter per job based on static `Settings` flags — there's no per-job retry-with-alternate-engine logic. Worth designing as: run primary engine → veraPDF check → if it fails or scores low, retry with the alternate engine before falling back to `manual_review` status. Not yet implemented; flagging here as a concrete enhancement to Workstream B's design rather than a fixed single-engine pipeline.

## Open questions (not yet decided)

- Where should real-mode testing actually run — install the system + ML dependencies directly on this dev machine, or build/run the Docker image (GPU-oriented; unclear if a GPU is available in this environment, though the Dockerfile comment says CPU-only PyTorch works too)?
- `InMemoryJobStore` (`api/store.py`) is explicitly a placeholder — job state is lost on restart. Fine for local testing, not for anything persistent.
- No `.env` exists yet in this repo (only `.env.example`) — `ANTHROPIC_API_KEY` needs to be set before the alt-text pass (`use_alt_text=True` by default) will work.
