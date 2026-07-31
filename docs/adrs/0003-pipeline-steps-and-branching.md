# 3. Remediation pipeline: artifact-based step architecture

## Status

Accepted

## Context

`process_remediation` is currently a placeholder. We need an architecture for the real pipeline: a sequence of stages, each independently skippable and failable, with enough recorded lineage to answer "what happened to this document" after the fact — not just a single pass/fail flag.

Which engine backs OCR/tagging (OpenDataLoader) is a related but separate decision — see [ADR 0004](0004-ocr-tagging-engine.md). This ADR is about the pipeline's shape: the stages, what each one is responsible for, and how outcomes are recorded — independent of which engine backs any given stage.

## Decision

The pipeline is a fixed sequence of responsibilities, each producing its own artifact:

1. **Validate input** — check the uploaded PDF against PDF/UA compliance before doing any other work. If it already passes, the job completes immediately without touching any other stage.
2. **Produce tagged structure and text** — OCR (where needed) and generate the PDF's tag tree in one pass. The initial conception included separate OCR and tagged structure steps, see ADR 0004 for why that is not possible that the moment.
3. **Normalize accessibility metadata** — fix up mandatory `MarkInfo`/`Lang`/title/tab-order gaps left by the previous stage; possibly skippable with a different OCR/tagging engine.
4. **Enrich figures** — generate alt text for untagged, non-decorative figures; skipped if there's no `/StructTreeRoot` or nothing left to enrich.
5. **Repair links** — provide struct-tree linking for `/Link` annotations.
6. **Validate output** — re-run the same PDF/UA check used at input. Passing completes the job; failing marks the whole job `FAILED`.

Each stage's outcome is recorded as a `RemediationArtifact` (`remediation`, `step`, `status` [completed/skipped/failed], `output_uri`, `error`) — one row per stage actually reached. `Remediation.source_pdf_uri` is never modified, so content-hash-based dedup keeps working regardless of how far a given attempt got; each stage instead writes its own `output_uri` and hands it to the next stage as input.

Artifact persistence exists to answer "which stage did a failed job die on, and what did each stage actually produce" without re-running anything — an audit trail and debugging aid. It is not, today, a resumability mechanism — see Failure semantics below for why.

### Failure semantics

- At this time, a failed stage fails the whole `Remediation` — there is no partial success and no "manual review" status between complete and failed.
- At this time, failed jobs do not resume, and stages are not individually rerun within one `Remediation`. If a retry mechanism existed, it would create a brand-new `Remediation` row and reprocess from stage one rather than resuming or reusing the failed attempt's artifacts — but no such mechanism exists yet, and resubmitting the same document does not create one automatically (see [ADR 0009](0009-no-retry-mechanism-yet.md), which supersedes this ADR's original assumption that dedup and retry-on-resubmit were the same thing).
- `RemediationArtifact` rows are immutable history once written — `(remediation, step)` is enforced unique at the database level, so a stage cannot be silently rerun and overwritten within the same attempt.
- This is a deliberate simplicity choice, not an oversight. True resumability (skip stages a *new* attempt has already redone, reuse a prior attempt's OCR output, etc.) is a real possible extension of this same artifact table, but nothing today requires it, and it adds real complexity (partial-attempt state, cache invalidation when an upstream stage's own logic changes). Revisit only if a real cost shows up — e.g. expensive OCR being redone on every retry of a large document.

### Open consideration: task redelivery vs. the per-step uniqueness constraint

The `(remediation, step)` uniqueness above assumes each step runs at most once per attempt because nothing redelivers a task mid-pipeline. That's true under `ImmediateBackend` today (see [ADR 0002](0002-task-execution-framework.md)), but not guaranteed once the production backend swaps to Cloud Tasks, which does at-least-once push delivery with retries. If `process_remediation` were redelivered mid-pipeline, the second run would hit this constraint as an `IntegrityError` on any step that already completed — which `tasks.py`'s current try/except/else would read as a real failure and call `mark_failed`, incorrectly failing a job that was actually still progressing fine. This is a different problem from the content-hash submission dedup in `RemediationService.latest_for_document` — that prevents duplicate *uploads* of the same document, not duplicate *task executions* of the same `Remediation`. Needs a real answer (e.g. treating an existing-artifact `IntegrityError` for the current step as a no-op rather than a failure) before that backend swap, not after.

## Consequences

- `Remediation` needed a per-stage outcome record instead of one flat `output_uri` field — see `RemediationArtifact` (`remediation/models.py`).
- Concrete service classes exist per stage (`PrecheckService`, `OCRService`, `TaggingService.finalize_tags`, `AltTextService`, `LinkService`, `PostCheckService`)
- This ADR doesn't decide which engine backs any given stage — see [ADR 0004](0004-ocr-tagging-engine.md) for OCR/tagging specifically.
