# 9. No retry mechanism yet; resubmitting a failed document no longer auto-retries

## Status

Accepted

## Context

Previously, `RemediationService.get_or_create_from_upload` used a dedicated `find_existing` lookup that excluded `FAILED` attempts when deciding whether a reusable job already existed for a document. That meant resubmitting a document whose last attempt had `FAILED` always auto-enqueued a brand-new `Remediation` and re-saved the file — so sending the same, unchanged file to the API multiple times could kick off multiple jobs for no deterministic reason, and (before [ADR 0008](0008-storage-layout.md)'s deterministic storage paths) duplicated identical bytes on disk each time.

Dedup ("is this the same document we've already seen") and retry ("re-attempt a failed job") are different concerns that this conflated into one.

## Decision

`get_or_create_from_upload` now looks up any existing attempt via `latest_for_document` — which was already the plain, un-filtered query, so this removed a redundant `find_existing` wrapper rather than adding new filtering logic. A `FAILED` attempt is "existing" like any other status. Resubmitting the same document reports that attempt's current state; it never auto-retriggers a job.

There is deliberately no way to actually retry a failed job yet. It doesn't make sense to design retry semantics before the rest of the pipeline (`finalize_tags`/`alt_text`/`link_tag`) exists for a retry to run through — today, "retry" would only ever mean "re-run precheck/ocr," not the full pipeline a real retry implies. Additionally, there is no point to retrying a failed document if nothing in the pipeline has changed, as the new attempt would apply the same steps, and therefore also fail.

If an altered document is uploaded, this is technically not a retry, as the content hash would be different - and so it would kick off a new remediation job.

## Consequences

- A document stuck on `FAILED` stays `FAILED` until a retry mechanism exists — there is currently no way to un-stick one, via the API or otherwise.
- Retry mechanism is future work. When it's built: keyed by `content_hash` (matching `document-status`'s existing document-scoped convention, not a specific `Remediation.id`), most likely creating a new `Remediation` row that reuses the failed attempt's `source_pdf_uri`/`content_hash`/`original_filename` without re-uploading, since [ADR 0008](0008-storage-layout.md) already puts the original at a deterministic path.
- **Open question, not decided here**: whether the trigger is a Django admin action or a real API endpoint. No models are registered in Django admin yet either way.
