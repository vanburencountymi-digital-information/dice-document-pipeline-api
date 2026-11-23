# 44. Sentry warning for noncompliant remediations

## Status

Accepted.

## Context

The backfill will produce many documents that finish but still fail postcheck. These are not errors ([ADR 0024](0024-split-failed-status.md)), so [ADR 0017](0017-sentry-error-monitoring.md)'s Sentry reports never fire for them. We want to sort these failures into categories, fix the pipeline once per category, and re-run the affected documents.

## Decision

- When a job ends `NONCOMPLIANT`, `process_remediation` sends one Sentry **warning** (not an error) through `NoncompliantReportService.report`, a small service of its own (kept apart from `RemediationService`, which is about job state, and from `ErrorLoggingClient`, which only knows how to talk to Sentry).
- The Sentry issue is grouped by the full set of failing rule IDs (`clause:test_number`, sorted). Documents failing the exact same rules are one issue with a count.
- Each event carries tags (`remediation_id`, `service_account_id`, `pipeline_version`) and a `details` block with the content hash (the document's ID), and each failing rule's ID, check count and severity.
- `ErrorLoggingClient.report_warning` gains optional `tags`, `fingerprint` and `context`. Calling it with only a message works as before. Rule IDs come from `FailedRule.rule_id`.

## Consequences

- A document failing 5 rules lands in a different issue than one failing 4 of the same 5. If categories turn out too fine, group per rule instead.
- The warning is sent just after the job's status is saved. If the worker dies between the two, the warning is lost (the redelivered task sees a finished job and skips it). We chose this over sending it twice, since a missing warning is the safer failure. Sending it before the status is saved would flip that.
- Postcheck crashes (`ERROR`) are unchanged: they are already reported as exceptions.
- Re-running is a separate step. The event gives the IDs needed to find the jobs.
