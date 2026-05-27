# Server Worker Refactor Plan

The Linear project needs the remediation pipeline to become a managed worker, not a desktop batch script. This plan keeps the existing working logic while changing the orchestration boundary.

## Target Shape

```text
WordPress / DICE Tool API
        |
        v
PostgreSQL job queue
        |
        v
Remediation worker
        |
        v
Object storage + PostgreSQL document records
```

## Phase 1: Extract Library Functions

Create a package such as:

```text
src/dice_document_pipeline/
  remediation/
    assess.py
    pikepdf_fixes.py
    ocr.py
    alt_text.py
    scoring.py
    logs.py
  workers/
    desktop_csv.py
    remediation_job.py
```

Move reusable logic out of `ada_remediate.py` while preserving the current desktop CLI as a thin wrapper.

## Phase 2: Define Job Contract

The server worker should accept a structured job:

```json
{
  "job_id": "uuid",
  "archival_object_id": "uuid",
  "source_pdf_uri": "object-storage-uri",
  "document_type": "minutes",
  "jurisdiction_id": "uuid",
  "title": "Human readable title",
  "sensitivity_class": "public",
  "exclude_from_kb": false,
  "pipeline_options": {}
}
```

It should return:

```json
{
  "job_id": "uuid",
  "status": "complete",
  "remediated_pdf_uri": "object-storage-uri",
  "compliance_score": 92,
  "compliance_grade": "A",
  "manual_review_items": [],
  "log_uri": "object-storage-uri",
  "pipeline_version": "semver-or-git-sha",
  "external_api_calls": []
}
```

Current package status:

- `src/dice_document_pipeline/workers/remediation_job.py` defines `RemediationJob` and `RemediationResult`.
- `process_remediation_job(job)` now supports local paths and `file://` URIs as a first package boundary.
- That first implementation assesses, scores, writes a log, and copies the source PDF to a local output directory when requested.
- It does not yet poll pg-boss, call Adobe PDF Services, read/write object storage, or callback to Cloud Run. Those remain DIC-156 infrastructure tasks.

DIC-156 adapter status:

- `src/dice_document_pipeline/workers/storage.py` defines storage read/write interfaces plus a local filesystem adapter.
- `src/dice_document_pipeline/workers/adobe_pdf_services.py` defines the Adobe Auto-Tag interface, a local no-network stub, and a lazy Adobe PDF Services SDK adapter based on the eval harness.
- `src/dice_document_pipeline/workers/server_worker.py` composes storage, Adobe, and the reusable package boundary.
- Real GCS, Adobe credentials, pg-boss, and Cloud Run callbacks should plug into these interfaces instead of replacing package logic.

## Phase 3: Replace Local State

Replace:

- `remediation_status.csv` with PostgreSQL job/document tables
- local `downloads/` with object storage reads
- local `remediated/` with object storage writes
- local text logs only with both text logs and structured JSON logs

## Phase 4: Add Audit And Cost Records

Every external call should record:

- provider and model/API
- call type
- input hash, not raw content
- document sensitivity class
- timestamp
- pipeline version
- estimated cost or transaction count
- success/failure summary

This feeds the Linear audit/governance workstream.

## Phase 5: Validate Against A Gold Set

Before replacing the desktop pipeline, build a 50-document validation set:

- scanned minutes
- born-digital minutes
- ordinances
- resolutions
- forms
- annual reports
- engineering drawings or maps
- long agenda packets
- table-heavy financial reports

Measure:

- processing time
- failure rate
- PAC/veraPDF results
- manual review time
- score distribution
- Adobe transaction usage if using PDF Services

## Non-Goals For The First Worker

- fully automatic legal certification
- autonomous publishing by an AI agent
- graph database modeling
- public natural-language querying

The first worker should reliably remediate, score, log, and return documents for human-governed publishing.
