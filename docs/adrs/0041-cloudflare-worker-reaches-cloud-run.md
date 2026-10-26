# 41. A Cloudflare Worker reaches Cloud Run

## Status

Accepted. Amends [ADR 0036](0036-origin-lock.md) — that ADR is not rewritten; this one records the change.

## Context

ADR 0036 assumed a Cloudflare Transform Rule would add the `X-Origin-Secret` header. But Cloud Run picks the service by the request's `Host` header, so a plain Cloudflare proxy to the `run.app` address fails, and a Transform Rule can't change `Host`. The other options were a Cloud Run domain mapping (Google lists it as Preview; DNS-only, so no Cloudflare WAF or rate limiting) and a Google load balancer with Cloud Armor (about $18+/month and tied to Google).

## Decision

- A Cloudflare Worker (`deploy/terraform/prod/worker/index.js`) receives requests on the public hostname and forwards them to the app's `run.app` address. It adds `X-Origin-Secret` and puts the public hostname in `X-Forwarded-Host`. It is managed in OpenTofu next to the Google setup, on Cloudflare's Free plan (100,000 requests a day).
- The Worker must send `Host: <service>.run.app` for Cloud Run to route the request. So Django now reads the public hostname from `X-Forwarded-Host` when `USE_X_FORWARDED_HOST` is on (default off). Prod turns it on; staging doesn't.
- That setting is only safe with the origin lock on, because only then can `X-Forwarded-Host` come from nothing but the Worker. The one unlocked URL, `GET /ping/`, just returns `pong!`.
- The secret is set on the Worker by hand, like the Secret Manager copy, so it stays out of OpenTofu's state. OpenTofu keeps it across applies.
- Staging has no Worker and no secret, so the lock is off there.

## Consequences

- The Worker can't be tried on staging. On prod, the order is: route through the Worker first, turn the lock on last. Turning the lock off again (an empty secret) is the rollback.
- On the Free plan, passing 100,000 requests a day cuts off the only path to the app until midnight UTC. Move to Workers Paid ($5/month) if the count gets close.
- Rotating the secret means changing it on the Worker and in Secret Manager together; requests fail in between (as in ADR 0036).
- Client IPs through the Worker depend on the Worker's requests reaching Cloud Run from a Cloudflare address and carrying `CF-Connecting-IP` ([ADR 0034](0034-cloudflare-ranges-in-code.md)). This is checked on prod before the work is closed.
