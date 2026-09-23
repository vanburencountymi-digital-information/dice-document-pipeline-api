# 20. Database-backed task worker

## Status

Accepted

## Context

[ADR 0002](0002-task-execution-framework.md) picked Django's built-in tasks framework and expected production to use a Cloud Tasks backend. Until now everything ran on `ImmediateBackend`, which runs a task inside the request. This is not suitable for production deploy.

Additionally, this team is small and wants to avoid lock-in to one cloud provider. Cloud Tasks has no equivalent on other hosts.

## Decision

Use [`django-tasks-db`](https://github.com/RealOrangeOne/django-tasks-db) (`django_tasks_db.DatabaseBackend`), instead of Cloud Tasks. Tasks are stored as rows in our existing Postgres database, and a separate, always-on worker process (`manage.py db_worker`) runs them. It's written by the author of the `django-tasks` project that Django's own tasks framework came from, and it supports delayed tasks (`run_after`), so webhook backoff now really waits.

- It's the default backend everywhere; `docker-compose.yml` adds a `worker` service, so local dev behaves like a deploy.
- Only tests use `ImmediateBackend` (set in `config/test_settings.py`), so they run tasks inline without a worker.
- This amends ADR 0002: the production backend is database-backed, not Cloud Tasks.

## Consequences

- Any host we deploy to needs a second always-on process for the worker, with automatic restarts. If it's down, uploads queue up but nothing gets processed.
- We give up Cloud Run's scale-to-zero for the worker. At our expected load (an initial burst, then a few jobs a day) an always-on small process is cheap, and the queue absorbs bursts.
- Finished task rows accumulate; `manage.py prune_db_task_results` needs to run on a schedule.
- A worker killed mid-task leaves that task unfinished; it isn't retried automatically. [ADR 0019](0019-idempotent-pipeline-steps.md) makes re-running a job safe when that's done by hand.
