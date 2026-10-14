# 27. Named tokens and token management in the admin

## Status

Accepted. Amends [ADR 0016](0016-knox-multi-token-auth.md) — that ADR is not rewritten; this one records the change.

## Context

Tokens had no names, so two tokens on one account could only be told apart by their first characters. They couldn't be revoked one at a time, and knox's own **Auth Tokens** admin page could create and delete tokens without going through `ServiceAccountService`. The webhook secret could never be changed.

## Decision

- New model `ServiceAccountToken` gives each knox token a name, unique within its service account. It points at the knox token; deleting the knox token deletes it too.
- `ServiceAccountService` is the only way to create or revoke a token: `create()` and `issue_token()` take a name, `revoke_token()` deletes one.
- Deleting a service account also deletes its login and tokens, through `ServiceAccountService.delete()`. Otherwise the tokens would keep working with no admin page left to revoke them.
- knox's **Auth Tokens** admin is removed. The service account page lists its tokens (name, first characters, created date), read-only.
- The service accounts list has no bulk actions. **Issue a new token** and **Regenerate webhook secret** are buttons on each account's own page, so they always act on one account. **Issue a new token** asks for a name. A new token is shown once in a click-to-select box.
- A new **Service account tokens** admin page lists all tokens. Deleting one there revokes it through the service. Tokens can't be added or edited by hand.
- **Regenerate webhook secret** asks for confirmation. The secret is read on the account page, not shown in the message bar.
- The migration does not name existing tokens. A token issued before it would keep working but have no name, so it would not be listed in the admin or be revocable there. Adding a service account asks for a name for its first token; there is no default, so every new token is named on purpose.
- No per-account token limit for now.

## Consequences

- One token can be cut off without touching the others.
- Revoking a token or regenerating the secret breaks the customer's integration until they update it.
- New migration `0004`; it only adds the new table. There is no backfill on purpose: staging's database is being replaced and local databases can be recreated, so no existing tokens need carrying over.
- `KNOX_TOKEN_MODEL` is set in settings, because knox's token model is swappable and Django needs it to link to it.
