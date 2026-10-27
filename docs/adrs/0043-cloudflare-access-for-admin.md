# 43. Cloudflare Access for the admin

## Status

Accepted.

## Context

`/admin/` is open to the internet, so anyone can try to guess a Django login. The app's own limits don't cover it yet ([ADR 0042](0042-cloudflare-waf-and-rate-limit.md)). Prod already sits behind Cloudflare, and about five people use the admin.

## Decision

- Cloudflare Access guards `/admin/` ([ADR 0041](0041-cloudflare-worker-reaches-cloud-run.md)'s hostname), made in OpenTofu (`deploy/terraform/prod/access.tf`). Requests without a valid Access login are stopped at Cloudflare and never reach Cloud Run.
- Login is a one-time PIN emailed to an address in `admin_emails` (a required variable, at least one address). No outside login provider (Google, Microsoft) is needed. It is free on Zero Trust's Free plan (up to 50 users).
- Django's own login stays, so the admin has two locks.
- Sessions last 8 hours.
- No Django change. The `run.app` address can't skip Access because the origin lock ([ADR 0036](0036-origin-lock.md)) already refuses it.
- The fallback of an IP allowlist is not built. It was only for Access not being workable.

## Consequences

- A one-time PIN only proves someone can read that inbox, so it is not true 2FA. If we need it, add Google or Microsoft as the login method.
- Anyone in `admin_emails` can open the Django login. Adding or removing a person is a reviewed change plus an apply.
- The API token needs two more Access permissions, and Zero Trust must be switched on for the account once, by hand.
- `/api-auth/login/` is not covered. It is still limited only by the app and Cloudflare's rate limit.
- Access trusts the browser's Cloudflare cookie, so a person who leaves the team keeps access until their session ends (up to 8 hours) unless removed and the session revoked in Zero Trust.
