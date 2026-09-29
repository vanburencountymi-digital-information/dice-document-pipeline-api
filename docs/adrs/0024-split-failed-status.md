# 24. Split `FAILED` into verdict statuses

## Status

Accepted. Amends [ADR 0013](0013-pipeline-continues-past-a-failed-step.md) and [ADR 0014](0014-versioned-retry.md) — neither is rewritten; this ADR is the record of the amendment.

## Context

`Remediation.JobStatus.FAILED` covered two different outcomes: a real crash, and postcheck finishing with real severity data but the document still having issues. ADR 0013/0015 already treat the second as worth serving, yet consumers saw the same `failed` for both and had to inspect `verification_results` to tell them apart. `COMPLETE` had a similar gap: it also covered a run where postcheck was disabled and nothing was verified.

## Decision

`JobStatus` is now `QUEUED`, `RUNNING`, `COMPLIANT`, `NONCOMPLIANT`, `ERROR`, `SKIPPED`:

- `COMPLIANT` — postcheck passed, or precheck found the document already compliant.
- `NONCOMPLIANT` — postcheck ran and found failed rules.
- `ERROR` — a crash, **including postcheck's own adapter failing** (new `PostCheckUnavailable`, no longer a `NotCompliant`): no verdict exists, so there's nothing to review.
- `SKIPPED` — the pipeline finished but postcheck is disabled, so there's no verdict.

`NONCOMPLIANT` and `ERROR` keep `error`, `final_output_uri` and `download_url`, and are both retry-eligible under ADR 0014's floor. `COMPLIANT` and `SKIPPED` are not retried. Breaking rename, no compatibility shim — there were no existing customers.

## Consequences

- Webhook/API consumers can branch on `status` alone.
- `wordpress-ada-pipeline-plugin`'s `Webhook::derive_badge()` still checks `'failed'`/`'complete'` and needs a matching update (separate repo).
