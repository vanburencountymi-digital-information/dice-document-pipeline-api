# 42. Cloudflare WAF and rate limit

## Status

Accepted.

## Context

The app limits callers itself ([ADR 0031](0031-api-throttling.md)), but nothing stops junk traffic before it reaches Cloud Run. Prod is on Cloudflare's Free plan, which allows one rate-limit rule, counted per IP over 10 seconds.

## Decision

- Managed WAF rules and a rate limit on `/api/` are made in OpenTofu (`deploy/terraform/prod/waf.tf`), not in the dashboard.
- The rate limit is 40 requests per 10 seconds per IP, then a 10-second block. It sits outside the app's 120 a minute, so the app still answers first for ordinary callers.
- `backfill_ips` lists IPs that skip the rate limit, for the WordPress host during the backfill. Empty by default.
- No body-size rule. Cloudflare already refuses bodies over 100 MB on Free and Pro, which is the limit wanted. If the app gets a lower maximum upload size, add a rule for it here.

## Consequences

- Until the app's own per-account limits cover everything, this is the only limit on `/admin/` and `/api-auth/login/`.
- The backfill's IP must be added before the backfill runs, then removed after.
- A zone allows one ruleset per kind; an existing one must be imported before the first apply.
