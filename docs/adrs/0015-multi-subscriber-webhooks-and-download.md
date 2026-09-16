# 15. `final_output_uri`, a download endpoint, and multi-subscriber webhooks

## Status

Accepted

## Context

Two things were missing before this: there was no way for a caller to actually retrieve a remediated PDF or be notified when a job finished, and the finished file itself had no persisted location — each pipeline stage writes its own `RemediationArtifact.output_uri`, but nothing designated which one was the last.

[ADR 0013](0013-pipeline-continues-past-a-failed-step.md) also means a `FAILED` job can still have a partially-remediated file worth serving — "no output at all" is no longer the correct default for a failed attempt.

## Decision

- `Remediation.final_output_uri` — set whenever the job reaches a terminal state (`RemediationService.mark_complete`/`mark_failed` both now require it), success or failure alike. `AlreadyCompliant` passes the untouched `source_pdf_uri`; every other terminal branch passes the pipeline loop's accumulated `pdf_uri`.
- **Per-attempt subscribers, not a field on `Remediation`**: a new `RemediationCallback` model (`remediation` FK `CASCADE`, `callback_url`, unique together) lets any number of callers register their own callback on the same attempt. `RemediationService.register_callback` is `get_or_create`-idempotent — resubmitting the same `(remediation, callback_url)` pair doesn't duplicate delivery.
- `POST /api/submit-document/` accepts an optional `callback_url`, registered against whichever `Remediation` the request resolved to (new or deduped). If that attempt is already terminal at submission time — subscribing after the fact — the notification fires immediately rather than waiting for a pipeline run that isn't going to happen.
- `GET /api/document-download/<content_hash>/` — same tenant scoping as `document-status` (`latest_for_document`). Serves the file whenever `final_output_uri` is set, regardless of `status` — a `FAILED` attempt is still downloadable.
- `send_webhook_notification(callback_id, attempt=1)` — keyed by `RemediationCallback.id`, not `remediation_id`, so each subscriber retries independently. `process_remediation`'s `finally` block (not a line after `try`/`except`/`else` — the generic `except Exception` branch re-raises) enqueues it for every callback registered on the attempt, once `mark_complete`/`mark_failed` has already committed the row's final state.
- `WebhookClient` (`remediation/webhook_client.py`) — single-attempt, HMAC-SHA256-signed POST via `ServiceAccount.webhook_secret` (generated in `ServiceAccountService.create()`, shared out-of-band like the auth token already is). Retry is the task's job, via re-enqueueing with `run_after`, not the client's.
- `download_url` — exposed on `RemediationSerializer` (whenever `final_output_uri` is set, not gated on `status == COMPLETE`) and in the webhook payload, both built through one shared `build_download_url` helper so the two can't drift.

## Consequences

- Supersedes `webhook_plan.md`'s single-callback design — that file is reduced to a pointer at the top, since its notify-and-download shape, signing scheme, and retry mechanics otherwise carried over unchanged.
- Real timed backoff for webhook retries only takes effect once a `run_after`-honoring task backend is in use — `ImmediateBackend` (this project's only backend today) ignores `run_after` and executes a re-enqueued attempt synchronously, so locally a failing webhook exhausts all `MAX_WEBHOOK_ATTEMPTS` back-to-back in one call, not with real delay between them.
- No rate limiting on `/api/submit-document/` — already tracked separately in `implementation_plan.md`.
- No real error tracking (e.g. Honeybadger) for swallowed step failures — a near-future follow-up, not built here.
