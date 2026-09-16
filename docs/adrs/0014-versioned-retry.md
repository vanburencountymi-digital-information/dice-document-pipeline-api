# 14. Versioned retry for failed remediations

## Status

Accepted

## Context

[ADR 0009](0009-no-retry-mechanism-yet.md) deliberately left retry unbuilt: at the time, "retry" would only have meant re-running precheck/OCR (the rest of the pipeline didn't exist yet), and there was no point retrying a failed document if nothing in the pipeline had changed — the new attempt would apply the same steps and fail the same way. Both of those conditions no longer hold. The full pipeline exists (and, per [ADR 0013](0013-pipeline-continues-past-a-failed-step.md), now runs to completion instead of aborting), and [ADR 0012](0012-automatic-semantic-versioning.md) gives every `Remediation` a `pipeline_version` — so "did the pipeline actually change since this failed" is now an answerable question, not a guess.

Two separate needs came up: a caller should be able to force a specific document to be reprocessed regardless of its current status, and — more importantly for scale — resubmitting a document that failed under an older pipeline version should be able to get a fresh attempt automatically, without every caller needing to know to pass a force flag after every release.

## Decision

`RemediationService.get_or_create_from_upload` gains `force: bool = False`. A new attempt is created (instead of reusing the existing one) when either:

- `force=True` — unconditional, regardless of the existing attempt's status. Exposed as an optional `force` field on `RemediationUploadSerializer`/`POST /api/submit-document/`, plumbed straight through by `CreateRemediationView`.
- The existing attempt is `FAILED` **and** its `pipeline_version` is strictly older than `PipelineConfig.retry_floor_version` (the ADR 0012 admin-editable singleton). Strictly older, not older-or-equal — an attempt that already ran on exactly the floor version reflects current logic and failed anyway, so re-running it would just replay the identical code against the identical original bytes.

`retry_floor_version` defaults to blank, which means nothing auto-retries until an admin deliberately raises it — bumping `PIPELINE_VERSION` via a routine release (ADR 0012 expects this to happen often, on nearly every merge) doesn't by itself cause anything to retry. Comparison is real semver (`packaging.version.Version`, already a dependency — not string comparison, which gets e.g. `"1.10.0" < "1.9.0"` backwards), via a small `_parse_version` helper that treats a blank/unparseable version (a pre-ADR-0012 row) as older than anything real rather than erroring.

A retry is a new `Remediation` row, not a resumed one — matches [ADR 0003](0003-pipeline-steps-and-branching.md)'s existing "no partial success, no step reruns within one attempt" model, and needed no schema change to allow: `Remediation` never had a DB-level uniqueness constraint on `(service_account, content_hash)`, only application-level "return the latest" logic. The original upload's storage path is deterministic per `(service_account, content_hash)` (ADR 0008), so a retry's `default_storage.exists()` check just finds the original already there and skips re-saving it — no special-casing needed to reuse it.

Deliberately **not built**: any bulk "retry every failed doc below the floor" mechanism. Raising the floor only changes what happens the next time someone resubmits a given document — it never proactively enqueues anything on its own. This is a conscious choice, not a gap to fill later by default — bumping the floor should never risk surprising anyone with a burst of retries (and their downstream cost — OCR, Claude Vision calls, etc.) they didn't explicitly ask for.

## Consequences

- Resubmitting a `FAILED` document with no `force` and no floor raised behaves exactly as ADR 0009 specified — reports the existing attempt, no new job.
- `PipelineConfig` (already built inert in ADR 0012) now has a real consumer.
- A caller integrating against this API can rely on stale failures eventually clearing on their own (once a floor is raised) without needing their own retry/backoff logic — but only if they resubmit; nothing pushes a retry to them.
- Supersedes [ADR 0009](0009-no-retry-mechanism-yet.md)'s "no retry mechanism yet" — that ADR's own text is left as historical record, not edited (matching how ADR 0004/0005/0006 stayed unedited when later amended).
