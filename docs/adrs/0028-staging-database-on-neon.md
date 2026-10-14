# 28. Staging database on Neon, worker every 20 minutes

## Status

Accepted. Amends [ADR 0023](0023-scheduled-batch-worker.md) — that ADR is not rewritten; this one records the change.

## Context

Staging's Cloud SQL instance cost about $40/month and was billed whether or not anything used it. Staging sits idle most of the day. The app only talks to Postgres through Django's ORM (`DATABASE_URL`), with no raw SQL or extensions, so the host can change freely.

## Decision

- Staging's database moves to [Neon](https://neon.com)'s free plan, one project used only by staging, in AWS `us-east-2` (closest to `us-central1`; Neon has no Google Cloud regions). Still Postgres. The data was not carried over.
- Compute is capped at 0.25 CU, so load can't scale it up and use the free hours faster.
- The app uses Neon's **direct** connection string, not the pooled one: connection counts are tiny, and this avoids pooling caveats.
- `CONN_HEALTH_CHECKS` is on, so a connection left over from before Neon went to sleep is checked before use.
- The worker schedule changes from every 15 minutes to every 20. Each run's "anything queued?" check wakes the database for about 5 minutes; at 20 minutes that is about 50 of the free plan's 100 compute-hours a month.

## Consequences

- Staging database cost goes from ~$40/month to $0; staging overall to roughly $10/month.
- An upload waits up to ~20 minutes for the next run, instead of ~15.
- "Turning staging off" is now just pausing the worker's scheduler trigger. The database sleeps by itself, so there's nothing to stop or restart.
- If usage nears the free limit, Neon suspends the database until the month resets. If the first week is on pace for over ~70 hours a month, move the worker to every 30 minutes. If we outgrow the free plan, Neon's paid plan is roughly $5–8/month.
- The first request after the database sleeps is slower while it wakes.
- Staging and production now run on different hosts; production's database is still undecided.
- Rollback: restore the previous `dice-database-url` secret version, reattach the Cloud SQL connection, start the instance, and set the schedule back to 15 minutes.
