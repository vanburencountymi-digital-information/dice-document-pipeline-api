# 35. Startup config warnings

## Status

Accepted.

## Context

Some missing settings don't stop the app from starting but make remediation quietly fail or lose files: no `S3_BUCKET_NAME` (files go to local disk, which Cloud Run discards and which the web and worker containers don't share), `RUN_ALT_TEXT` on without `ANTHROPIC_API_KEY`, `RUN_OCR` on without `OPENDATALOADER_HYBRID_URL`. DIC-2321 proposed refusing to start. But people deploy this repo in different ways: local disk can be legitimate, and any step can be switched off on purpose. Settings that need a step to be on shouldn't be demanded when it's off.

## Decision

- On startup, `CommonConfig.ready()` reports a warning, through `ErrorLoggingClient`, for each of the three problems above. Nothing refuses to start.
- The API key and OCR URL are only checked when their step is on.
- `OPENDATALOADER_HYBRID_URL` no longer defaults to `http://localhost:5002`; unset is empty, so it can be detected. `docker-compose.yml` still sets it.
- Not checked: `ALLOWED_HOSTS` (Django already fails loudly), and `RUN_*` flags (unset and `False` behave the same).

## Consequences

- A bad deploy still goes live; the warning is what flags it, so Sentry must be watched. With `SENTRY_DSN` empty (local, tests) the warnings do nothing.
- Each process (web, worker, commands) reports its own warning at startup; Sentry groups identical ones.
- Missing config that makes a run fail is still reported by that run's own error.
