# 29. Staging worker paused by default

## Status

Accepted. Amends [ADR 0028](0028-staging-database-on-neon.md) — that ADR is not rewritten; this one records the change.

## Context

[ADR 0028](0028-staging-database-on-neon.md) moved staging to Neon's free plan and set the worker to run every 20 minutes, using about half the free compute-hours. In practice staging is only used in short bursts of manual testing, so even that is wasted hours.

## Decision

- The `dice-worker` scheduler trigger exists but stays **paused** on staging. The worker is run by hand (**Cloud Run → Jobs → `dice-worker` → Execute**) after a deploy or during testing.
- Resuming the trigger still gives the 20-minute schedule from ADR 0028.
- The worker reads the same `dice-database-url` secret as the app and migrations job, and has no Cloud SQL proxy container. The separate `dice-database-url-tcp` secret is no longer used.

## Consequences

- Uploads on staging wait until someone runs the worker. Nothing is lost; callers get their webhook once it runs.
- Neon uses compute-hours only when the app, API or a manual run touches it.
- The free plan can only restore to a point in the last 6 hours, with no nightly backups. Staging data must be treated as disposable. If Neon is ever considered for production, it needs a paid plan with a longer history window, plus nightly dumps to storage outside Neon (R2 works, as it is S3-compatible).
- Corrected rollback (replaces ADR 0028's): put the previous `dice-database-url` secret version back, reattach the Cloud SQL connection to `dice-app`, `dice-worker` and `dice-migrate`, re-add the `cloud-sql-proxy` container to `dice-worker` with its `dice-database-url-tcp` secret, start `dice-staging`, and resume the trigger (set to every 15 minutes if going back to ADR 0023).
