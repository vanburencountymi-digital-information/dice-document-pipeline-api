# 25. Admins create organizations and service accounts in the admin site

## Status

Accepted. Amends [ADR 0016](0016-knox-multi-token-auth.md) — that ADR is not rewritten; this one records the change.

## Context

ADR 0016 turned off the admin's `Add` button for service accounts, so a plain save couldn't skip making a token. `Organization` wasn't in the admin at all. The result: every new customer needed a shell or a one-off Cloud Run job.

## Decision

- Admins can add an **Organization** in the admin. The form also asks for a **service account name**, typed by hand (no default), and creates both together. The first token is shown once at the top of the page.
- **Service account** `Add` is back, with a short form (organization + name). Saving always goes through `ServiceAccountService.create()`, so the login, webhook secret and first token are never skipped.
- Organization names are now unique. A service account's name becomes its login username, so a name that is already taken gets a form error instead of a crash.

## Consequences

- No shell or Cloud Run job is needed to onboard a customer.
- The migration fails if production already has two organizations with the same name. Check first.
- Token handling is unchanged: shown once, not stored, more can be issued from the admin.
