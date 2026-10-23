# 37. Health check endpoint

## Status

Accepted. Amends [ADR 0036](0036-origin-lock.md).

## Context

Cloud Run's probes, the promotion smoke test and the backfill's "wake the pipeline" step all need a URL that answers when the app is running. ADR 0036 said there was no health-check URL, so nothing was exempted from the origin lock. Cloud Run's probes call the app directly, so they carry no `X-Origin-Secret`.

## Decision

- `GET /ping/` is a normal view (`PingView`) that returns `pong!` as plain text. It needs no login and checks nothing else, so it only says the app is running.
- The origin lock lets `GET` and `HEAD` of this one URL through without the secret. Other methods stay locked.
- It uses the usual per-IP throttle ([ADR 0031](0031-api-throttling.md)), since it is a public URL. Each call writes a throttle counter, so it needs the database.
- It is left out of the API docs.

## Consequences

- `/ping/` is the one URL the origin lock does not protect. Anyone who finds the raw `run.app` address can call it, with no Cloudflare in front. The only limit there is the per-IP throttle, which many addresses can get around, each call costing a database write.
- The deployment-level limit for this gap is Cloud Run's maximum instances (and concurrency), set when the service is deployed. Traffic is expected to be low, so no further limit is planned.
- Because each call uses the database, it also wakes a sleeping Neon database ([ADR 0028](0028-staging-database-on-neon.md)). If the database is down, `/ping/` fails too.
- The host check still applies. Cloud Run's probes must send a `Host` listed in `ALLOWED_HOSTS` (set it in the probe's HTTP headers).
- Probes count toward the per-IP limit. Normal probe rates are far below it.
- The URL has the trailing slash; `/ping` redirects to it.
- The https redirect is not exempted. If a Cloud Run probe turns out to arrive over plain http and be redirected, add `SECURE_REDIRECT_EXEMPT = [r"^ping/$"]`.
