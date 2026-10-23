# 36. Origin lock

## Status

Accepted. Amends [ADR 0021](0021-deployable-image.md).

## Context

Cloudflare sits in front of the app, but Cloud Run also answers on its own `run.app` address. Anyone who finds it can skip Cloudflare's protections. Django also trusts `X-Forwarded-Proto` unconditionally (`SECURE_PROXY_SSL_HEADER`), which is only safe if every request really came through a proxy we control.

## Decision

- A small middleware, `common.middleware.OriginLockMiddleware`, is first in `MIDDLEWARE`. If `ORIGIN_LOCK_SECRET` is set, any request whose `X-Origin-Secret` header doesn't match (compared in constant time) gets a plain `403`.
- Cloudflare adds that header to every request with a Transform Rule (Modify Request Header).
- If `ORIGIN_LOCK_SECRET` is empty, the middleware does nothing. That is the default, so local development and hosts without Cloudflare are unaffected.
- In production the secret is a Secret Manager secret (`dice-origin-lock-secret`) like the others.
- There is no health-check URL, so nothing needs to be exempted.

## Consequences

- A deployment that forgets to set the secret is not warned, and stays open (same trade-off as [ADR 0033](0033-no-proxy-hops-startup-warning.md)).
- Rotating the secret means changing it in Cloudflare and in the app; requests fail in between unless both are done together.
- Once the lock is on, `X-Forwarded-Proto` can only arrive via Cloudflare, which closes the gap above.
- Cloud Run's routing by `Host` header must still be checked on the real deployment (DIC-2324 / 5-1): Cloudflare has to send the custom domain's `Host` for Cloud Run to route to the service.
