# 17. Sentry for error monitoring

## Status

Accepted

## Context

There's no error-tracking tool in this repo at all today — the only visibility into a failure is console logging. Two problems follow from that as we get ready to deploy:

- An unexpected exception in `process_remediation` currently propagates, uncaught, all the way back through `CreateRemediationView` to a bare Django 500. Nobody finds out unless someone is watching logs at the time.
- Every pipeline step already catches and continues past its own failures (ADR 0013), by design — but that means a real, recurring problem in one step could keep happening silently, since it never surfaces as anything louder than a log line.

We want visibility into both before making other changes (a real storage backend, a real worker) that could introduce new failure modes of their own.

## Decision

Use Sentry (`sentry-sdk[django]`). `sentry_sdk.init()` is called unconditionally in `config/settings.py`; an unset `SENTRY_DSN` (the local/test default) makes it a safe no-op, so no extra "is monitoring enabled" flag is needed.

Unhandled exceptions are caught automatically via Sentry's Django integration — no code changes needed at the call sites that already let exceptions propagate. Additionally, every pipeline step's own swallowed-and-continued failure (`VerificationService`, `OCRService`, `FontRepairService`, `MetadataService`, `LinkService`, `AltTextService`, `ScoringService`, all in `remediation/services.py`) now also calls `sentry_sdk.capture_exception()` alongside its existing `LOGGER.exception()`, tagged with `step` and `remediation_id` so these are groupable/filterable rather than one undifferentiated stream.

## Consequences

- `PIPELINE_VERSION` (previously baked into settings but otherwise unused) is now Sentry's `release` tag.
- Tagging every swallowed per-step failure, not just the two previously-unguarded fatal paths, is a deliberate choice for maximum visibility up front. If this proves noisy in practice, it can be dialed back to only the fatal paths without any structural change.
- No new infra — this is a SaaS dependency (an outside API), not a swappable adapter; there's no `RemediationArtifact` tracking or per-step contract here, so it doesn't follow the `-Adapter`/`-Client` naming convention.
