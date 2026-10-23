# 38. Worker-only OCR URL warning

## Status

Accepted. Amends [ADR 0035](0035-startup-config-warnings.md).

## Context

ADR 0035 warns at startup, in every process, if `RUN_OCR` is on without `OPENDATALOADER_HYBRID_URL`. In Cloud Run only the worker job has the OCR sidecar and the URL; the web app and migrate job share `RUN_OCR` but never call OCR. They sent a false warning on every start.

## Decision

- The OCR URL check moves out of the startup warnings into `worker_config_warnings()`.
- `run_queued_tasks` reports it, only when work is queued, just before it would use OCR.
- The other startup warnings are unchanged.

## Consequences

- No false OCR warnings from the web app or migrate job.
- A worker started another way (e.g. `db_worker` directly) doesn't report it; `docker-compose.yml` sets the URL there.
- An idle worker run says nothing, so a missing URL shows only once work is queued.
