# 2. Task execution framework for the remediation pipeline

## Status

Accepted

## Context

Remediation (veraPDF check, possibly Docling OCR, OpenDataLoader tagging, possibly Claude Vision alt-text) can't run synchronously in `CreateRemediationView`. We need it to run out-of-band, be free to run locally (no broker, no extra process), and, at the moment, deploy cleanly to Cloud Run — which scales to zero, so anything relying on an always-on polling worker is out; delivery needs to be push-based.

## Options

**Celery / django-rq** — the default choice, rejected: needs a persistent Redis broker and always-on workers, which fights scale-to-zero and adds real ops overhead for a single job type.

**Google Cloud Tasks directly** — a good fit for Cloud Run (push-based, built-in retries) but couples application code to the Cloud Tasks SDK, and there's no official emulator for local dev. `django-google-cloud-tasks` (has an eager/sync local mode) is workable but is a large framework for what we need; `GeorgeLubaretsi/django-cloud-tasks` is alpha and explicitly not secure — ruled out. No lighter "just run it inline" package appears to exist for this.

**Django's `django.tasks` (Django 6, DEP 0014)** — first-party, backend-agnostic: `@task`-decorated functions, `.enqueue()`, a `TASKS` setting picks the backend. Ships `ImmediateBackend` (sync, in-process — zero infra for local dev) and `DummyBackend` (test-only). Custom backends implement `BaseTaskBackend`, so a Cloud Tasks-backed one can be swapped in later via config alone — mirrors the swappable-storage pattern already used for `django.core.files.storage`.

## Decision

Use `django.tasks`. `TASKS["default"]["BACKEND"]` is `ImmediateBackend` locally; on Cloud Run it swaps to a Cloud Tasks-backed `BaseTaskBackend` via an env var, with no business-logic change. Task code lives in `remediation/tasks.py` — the only file in the app importing `django.tasks` — and calls into `RemediationService` rather than duplicating state-transition logic.

## Consequences

- No new infra for local dev or early production — the deciding factor over Celery/RQ.
- Not locked to Google Cloud Tasks or GCP; swapping backends is a config change.
- Betting on a newer Django API, mitigated by `ImmediateBackend` acting like a plain function call and the backend boundary containing any future swap to `remediation/tasks.py` + the `TASKS` setting.
- Updates to the tasks backend will essentially come for free with updated versions of Django.
- Caveats: (1) task args must be JSON-serializable — pass `remediation_id`, not the model instance; (2) wrap `.enqueue()` in `transaction.on_commit()` when called inside `atomic()`; (3) no official Cloud Tasks emulator — `aertje/cloud-tasks-emulator` (community) if production-parity local testing is ever needed.
