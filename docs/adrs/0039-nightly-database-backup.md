# 39. Nightly database backup

## Status

Accepted. Follows [ADR 0029](0029-staging-worker-paused-by-default.md), which said production on Neon would need nightly backups to storage outside Neon.

## Context

Neon only restores to a recent point in time. One bad day, or a Neon-side problem, shouldn't be able to take the only copy of prod's data (DIC-2540). Where to run the backup, and how to build it, were open.

## Decision

- Use the [`django-dbbackup`](https://github.com/Archmonger/django-dbbackup) package (pinned `5.4.0`) instead of our own `pg_dump` code. It already runs `pg_dump`, uploads through a Django storage, names files, deletes old ones and can restore. About 200,000 downloads a month, Django 6 support, regular releases; it has a single main maintainer, so if it is abandoned the fallback is a small command of our own around `pg_dump`.
- `python manage.py backup_database` is a thin wrapper (`DatabaseBackupService`, `DbBackupAdapter`). It checks that a backup bucket is set up, runs `dbbackup`, and reports any failure to Sentry with the tag `job: database_backup`. The package's own email alerts are off, and its log record of the same failure is ignored in Sentry, so there is one event per failure.
- The Postgres connector is `PgDumpBinaryConnector` (compressed `pg_dump` custom format, restored with `pg_restore`). `pg_dump` is started with only `PATH` and its connection variables, so it never sees the bucket token. `postgresql18-client` is added to the app image; it must be at least as new as the server.
- It runs as a Cloud Run job, `dice-db-backup`, started every night by Cloud Scheduler and managed in OpenTofu like `dice-worker`. Same app image, so `deploy-prod.sh` updates it with the rest. GitHub Actions was rejected: it would copy the full-access database URL into GitHub, and its schedules can run late or switch off.
- The bucket is separate from the documents bucket, with its own token that only the backup job holds. The job runs as its own service account (`dice-prod-db-backup`), the only one that can read the backup secrets, so code running in the app or worker (which handle uploaded files) can't read the backup token.
- The backups go through a second Django storage, `STORAGES["dbbackup"]`, which exists only when `DB_BACKUP_BUCKET_NAME` is set. The package falls back to local disk without it, so the service refuses to run when the entry is missing.
- Retention is the newest **14** backups (`DBBACKUP_CLEANUP_KEEP`), deleted by the command after each backup. Counting backups, not days, means a run of failed nights never deletes the last good ones. This replaces the R2 lifecycle rule first planned, so the token needs permission to delete.
- Restoring uses the package's `dbrestore` into an empty database, without `--clean`, so it never drops existing tables.
- Encryption: R2 encrypts stored objects, and that is judged enough for now; no extra layer is added.

## Consequences

- A missing run (the job never started) isn't reported; only failures are. Watching Cloud Run job failures and adding a Sentry cron monitor are follow-ups.
- The app image grows by the Postgres client, and one pinned package is added.
- Retention counts backups, so extra manual runs shorten the window. Two runs overlapping (a manual run during the scheduled one) would each trim the bucket; at worst one fails on a double delete. Cloud Run has no setting that prevents it, and it can't lose the newest backups.
- Each backup is two objects (the backup and a small `.metadata` file); the package deletes both.
- The dump is only checked by `pg_dump` exiting cleanly; nothing opens it. A restore test (ticket 8-11) is the real check.
- Restoring into a database that has tables fails on purpose. Restoring into Neon should be tried once, in case of ownership errors (the package's open issue #495).
