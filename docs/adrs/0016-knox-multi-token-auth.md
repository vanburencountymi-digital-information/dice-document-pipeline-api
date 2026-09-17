# 16. django-knox replaces DRF token auth: multiple, hashed, expiring tokens per service account

## Status

Accepted

## Context

Since [ADR 0001](0001-fastapi-vs-django.md), API auth has run on DRF's `rest_framework.authtoken.Token`: one row per `User`, minted once in `ServiceAccountService.create()`, read back via `ServiceAccount.token`. This was a temporary workaround for development that is now being changed as we are getting ready to deploy, as standard DRF token issuance has three main issues:

- The token is stored plaintext, vs hashed-and-verified.
- DRF Token is one per User. As a result, rotating a token means regenerating the only one, breaking existing integrations with no overlap window.
- Tokens never expire.

## Decision

Swap `rest_framework.authtoken` for `django-knox`, which provides:
- multiple tokens per account
- tokens are hashed at rest, with raw value returned only once at issuance
- sliding 1 yr expiry: `REST_KNOX["TOKEN_TTL"] = timedelta(days=365)` with `AUTO_REFRESH = True` — an actively-used token's expiry keeps sliding forward, but one that goes unused for a year stops working.
- issurance via django admin or shell only; `Add` default in django-admin is disabled.

## Consequences

- Every existing plaintext DRF token stops working the moment this ships.
- `ServiceAccount.token` (a property reading `user.auth_token.key`) is removed outright — there's no longer a single token to point to, and a real token's value can't be read back after issuance anyway. Tests get a purely in-memory `token` attribute on `ServiceAccountFactory`-built instances for convenience; production code has no equivalent.
- There is still no self-serve API for a service account to list, label, or revoke its own tokens — token lifecycle management remains an ops/admin operation, same posture as account creation itself today. Worth revisiting if integrators start asking for self-service rotation.
- [ADR 0001](0001-fastapi-vs-django.md)'s "Auth: `django.contrib.auth` + DRF `TokenAuthentication`, already wired to `ServiceAccount.token`" line is superseded by this decision; ADR 0001's own text is left as historical record, not edited (matching how ADR 0004/0005/0006/0009 stayed unedited when later amended).
