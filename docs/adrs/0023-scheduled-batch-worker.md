# 23. Scheduled batch worker

## Status

Accepted. Amends [ADR 0020](0020-database-backed-task-worker.md).

## Context

[ADR 0020](0020-database-backed-task-worker.md) runs the queue with an always-on worker process. On Cloud Run, the worker also needs the OCR server beside it (a sidecar, ~4 CPUs / 14 GiB together), and an always-on instance that size costs roughly $260/month — most of the staging bill. Expected load is a few documents a day, about 15 minutes of real work, so almost all of that is paying for an idle machine.

## Decision

In deployed environments, the worker is a **Cloud Run job that Cloud Scheduler starts every 15 minutes**, running `manage.py run_queued_tasks`:

1. If nothing is queued (`TaskQueueService.has_ready_tasks()`), exit straight away — seconds, almost free. Most runs end here.
2. Otherwise, if OCR is on, wait for the OCR sidecar's `/health` to answer (`OpenDataLoaderHybridClient`), up to `OCR_READY_TIMEOUT_SECONDS`. If it never does, fail the run; tasks stay queued for the next one.
3. Run django-tasks-db's worker in batch mode (`db_worker --batch`): everything that's due, then exit.

The job has **no** container startup dependency on the OCR sidecar — that would make every empty run wait for OCR to load. The command waits for OCR itself, only when there's work.

The queue itself is unchanged: still Postgres via django-tasks-db. Local development keeps the always-on `db_worker` in docker-compose for instant feedback.

## Consequences

- Worker cost drops from ~$260/month to roughly $5–10 at the expected load; staging overall to ~$20–25/month.
- An upload waits up to ~15 minutes for the next run, plus a minute or two for OCR to load. Acceptable because callers are notified by webhook, not kept waiting on a response.
- Webhook retry backoff ([ADR 0015](0015-multi-subscriber-webhooks-and-download.md)) is at least the schedule interval — a retry due in 30 seconds goes out on the next run.
- Every run that has work waits for OCR first, so the "OCR not loaded yet" problem of an always-on worker coming back up doesn't arise.
- Overlapping runs (a long batch still going when the next one starts) are safe — django-tasks-db locks each task, so nothing runs twice — just costlier.
- A run stopped mid-document (e.g. by the job's 60-minute task timeout) leaves that document at `running`; nothing re-queues it yet — the same gap as before.
- Could later add "start the job right away when a document is uploaded" to cut the wait, keeping the schedule as a safety net.
- **Once production is live, staging's schedule gets drastically less frequent** — or is paused, with the worker run by hand only after a new deploy or during manual testing. The 15-minute interval is for the period when staging is where real testing happens.
