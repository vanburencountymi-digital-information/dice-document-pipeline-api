# 26. Add organizations and service accounts separately

## Status

Accepted. Amends [ADR 0025](0025-admin-creates-organizations-and-accounts.md) — that ADR is not rewritten; this one records the change.

## Context

ADR 0025 made one form create an organization and its first service account together. That needed custom checks, because a service account's name was also its login username: a taken name had to be caught by hand, or it crashed with a database error.

## Decision

- Organizations and service accounts are added separately in the admin. The organization form is Django's default.
- A service account's login username is now generated (`sa-` plus random characters). Its name is only a label, so it can never clash with another user.
- Service account names are unique **within an organization** (a database constraint). Django's admin form checks it and shows a normal error, so no custom checks are needed.
- Service account `Add` still goes through `ServiceAccountService.create()`, so the login, webhook secret and first token are never skipped. It asks only for the organization and name.

## Consequences

- Adding a customer takes two steps instead of one.
- Two organizations can use the same account name (e.g. both `wordpress-prod`).
- New migration `0004`. It fails if production already has two accounts with the same name in one organization — check first.
- Existing accounts keep their current usernames.
