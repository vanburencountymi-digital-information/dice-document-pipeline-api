# 19. Idempotent pipeline steps

## Status

Accepted

## Context

[ADR 0003](0003-pipeline-steps-and-branching.md) left an open consideration: `process_remediation` assumes it runs at most once per `Remediation`. That holds under `ImmediateBackend`, but not once a real worker backend is in place — a worker crash or a re-enqueued task can run the same job again. A second run would hit the one-row-per-step uniqueness on `RemediationArtifact` and `VerificationResult` (and `RemediationScore`'s one-to-one), and that `IntegrityError` would fail a job that was actually fine.

## Decision

- **Steps skip finished work.** `ArtifactService.completed_output_uri()` returns a step's `output_uri` if it already `COMPLETED` for this remediation; every transform step returns it straight away without calling its adapter. `FAILED`/`SKIPPED` steps run again.
- **Verification steps replay their verdict.** Precheck/postcheck signal their result by raising `AlreadyCompliant`/`NotCompliant`, so a plain early return would let the pipeline carry on past a decision already made. Instead they rebuild the stored `VerificationResult` and call `handle_result()` again.
- **Artifact rows are upserted** (`update_or_create`), so a re-run of a previously failed step overwrites its row rather than crashing.
- **Finished remediations aren't re-run.** `process_remediation` returns immediately if the row is already `COMPLETE` or `FAILED` — no timestamp changes, no second round of webhooks. Retries are unaffected, since they always create a new row ([ADR 0014](0014-versioned-retry.md)).

## Consequences

- Closes ADR 0003's open consideration; required before the database-backed worker.
- A step that crashed mid-way (e.g. the process was killed) has no `COMPLETED` row, so it runs again from scratch — `persist_output` already overwrites its deterministic output path in place ([ADR 0018](0018-s3-compatible-storage-backend.md)).
- A remediation stuck in `RUNNING` after a hard crash is not detected or re-enqueued automatically; that's left for a later change.
