# 31. API throttling

## Status

Accepted. Enforces the submission limit stored by [ADR 0030](0030-per-account-limits.md). Closes the "no throttle classes" gap noted in [ADR 0015](0015-multi-subscriber-webhooks-and-download.md).

## Context

The submit endpoint had no limits. Each account already stores its limits (ADR 0030); something has to enforce them, slow down token guessing, and tell callers (like the bulk backfill runner) what their limits are.

The app runs on Cloud Run, sometimes behind Cloudflare (production) and sometimes not (staging), with several instances at once. So limits must not depend on one instance's memory or on how a request happened to arrive.

## Decision

- **Submission limit.** `POST /api/submit-document/` counts the account's own jobs made in the last period (a sliding window) and answers `429` if the limit is used up. There is no separate counter to drift: the jobs are the count.
  - The service runs the check only once it knows a new job is needed (after looking for an existing one), and the check and the job are made in one transaction with the account's row locked, so two requests at the same moment can't both squeeze under the limit.
  - Only a new job counts. A repeat of an already-submitted file (the `200`) makes no job, so it is free, and is never turned away even if the account is at its limit (so a client that timed out and retries gets its job back). `force=true` counts. A rejected request creates nothing.
  - A limit of 0 rejects everything. An unlimited account is never throttled. An account with half-filled limits gets `403` on every endpoint (`IncompleteLimitsError`), never "unlimited". Saving such an account is refused anyway.
- **The `429`** carries `Retry-After` (seconds) and a JSON body: `limit` (`"rate"`), `retry_after`, `remaining`, `resets_at`.
- **Headers on every response** for a limited account: `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset` (seconds). This is how a caller such as the bulk runner learns its limits, so it can keep its own cap at or below the server's. The in-flight cap is not reported yet (it is not enforced yet).
- **One unfinished attempt per document.** A database rule allows only one queued or running attempt per account and document. If the same file arrives twice at once, or `force` is used while a job is still running, the existing job is returned (a `200`) instead of making a second one.
- **Lost jobs are queued again.** A job with no progress for too long (queued for `STUCK_QUEUED_MINUTES`, default 60, just over the scheduled worker's gap; running for `STUCK_RUNNING_MINUTES`, default 180, about the longest a job can take) is assumed to have lost its task (the process died before queueing, or a worker died). When its document is resubmitted, including with `force`, the existing job is queued again and returned, instead of staying stuck forever behind the unfinished-attempt rule. Being queued again is recorded on the job (`last_enqueued_at`) with a single conditional update, so two resubmits at the same moment can't both queue it, and it isn't queued again until another full wait has passed. Nothing sweeps for stuck jobs on its own; a resubmit is what triggers it. This answers the open point in [ADR 0019](0019-idempotent-pipeline-steps.md) that a job stuck in `RUNNING` after a crash is not re-queued, but only when someone resubmits.
- **Failed logins.** A wrong token counts against the caller's IP address (default `100/hour`, `FAILED_AUTH_RATE`). Past the limit that IP gets `429`, even with a valid token. Requests with no token, and valid tokens, are not counted.
- **Per-IP backstop.** Every endpoint, including the unauthenticated docs and schema pages, is also capped per IP address (default `120/minute`, `CLIENT_IP_RATE`). It is a safety net, not a quota. Unlimited accounts (ADR 0030) are skipped, so "no limits" really means none; the failed-login limit still applies to them.
- **Real IP.** Both IP limits use the caller's real address, however the request arrived:
  - Behind the hosting platform's proxy only (Cloud Run without Cloudflare): the address the platform saw connecting, which is the last `X-Forwarded-For` entry and the only one a caller can't set. `TRUSTED_PROXY_HOPS` says how many such entries the platform adds (1 on Cloud Run). It defaults to 0, which ignores the header, so a deployment on a different host is safe until it is configured, and moving host is a one-line setting.
  - Through Cloudflare: that address is Cloudflare's, so the `CF-Connecting-IP` header is used, but only when the connecting address is in Cloudflare's published ranges (`CLOUDFLARE_IP_RANGES`). Otherwise anyone could fake it.
  - The result is always a valid IP address (or `unknown`), so it is safe in a database key. An IPv6 caller is identified by its /64 network, since one caller usually controls a whole /64 and could otherwise use a fresh address for every request.
  - The app logs a warning at startup if `TRUSTED_PROXY_HOPS` is 0 outside local development, since callers behind a proxy would then share one counter.
  - Django REST framework's own `NUM_PROXIES` is not used: with fewer `X-Forwarded-For` entries than expected it falls back to an entry the caller wrote, and it doesn't check the entry is an IP address.
- **Counts for the two IP limits** live in a small database table (`ThrottleCounter`, one row per IP and limit), updated with a single atomic statement, so every instance shares exact counts. Expired rows are cleaned up when new rows are made. These are fixed windows (a window starts at an IP's first request and lasts one period). No off-the-shelf package fits: DRF's built-in throttles count in Django's cache (per-process by default, so not shared across instances), and `django-axes` and `django-ratelimit` don't fit Knox token failures keyed on a real IP or a check-now, count-later flow.

## Consequences

- The IP limits write to the database on every request (the backstop) or every failed login.
- The failed-login lockout checks first and counts after the token is tried, so a burst of simultaneous wrong tokens from one IP can all pass the check before any is counted. The overshoot is the number of requests that IP can have in flight at once, on top of the limit; the counts themselves are exact. This is accepted: Knox tokens are long and random, so guessing one is not practical, and counting first would add a database write to every request. Revisit if tokens ever become guessable.
- Re-queueing a job that is still running (past `STUCK_RUNNING_MINUTES` but its worker is alive and just slow) can run it twice. Redelivered tasks are meant to be safe to repeat (ADR 0019), but the task has no lock, so two runs at the same moment would both do the work.
- Making a job holds the account's row lock while the upload is stored (the service runs the whole decision in one transaction), so one account's uploads are made one at a time. The backfill sends one at a time, so this should not slow it.
- If the list of Cloudflare ranges goes stale, traffic from a new range is treated as coming from Cloudflare's own address: it shares one counter, but can't be faked. Compare the setting against Cloudflare's published list now and then.
- An office sharing one IP address shares the failed-login and backstop counts.
- `max_in_flight` is not enforced yet; that is a separate change.
- Cloudflare's rate-limit rule (ticket 5-3) remains the outer layer. `/admin/` and `/api-auth/login/` are not Django REST framework views, so only that layer covers them.
- Per-account limits on status and download are not added (DIC-2233 is still open); only the per-IP backstop covers them.
- Deploying needs migrations: the new `ThrottleCounter` table, the unfinished-attempt rule and an index and a `last_enqueued_at` field on `Remediation`. Existing data with two unfinished attempts for one document must be cleaned up first or the rule can't be added.
