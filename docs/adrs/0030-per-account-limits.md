# 30. Per-account limits

## Status

Accepted.

## Context

A backfill for one website needs far higher limits than a normal account, and that should not need a code change. The submit endpoint has no limits yet; they come in later tickets.

## Decision

- Each `ServiceAccount` stores two limits and an unlimited switch, editable in the admin (on Add and Edit):
  - `max_in_flight` (shown as "Maximum submissions allowed at once, queued and working"): most documents waiting or being worked on at once (default 10, from the `DEFAULT_MAX_IN_FLIGHT` setting).
  - `rate_limit_count` and `rate_limit_period` (shown as "Submission limit per time period" and "Time period"): most submissions per second, minute, hour or day (default 100 per day, from the `DEFAULT_RATE_LIMIT_COUNT` setting; the period defaults to day). The period is a dropdown, so it can't be mistyped.
  - `unlimited` (shown as "Service Account has no limits"): skips both limits, for large one-off jobs. The stored values are kept for when it is unticked.
- Unlimited is only ever this checkbox: no `-1` or `9999` value, and blanking a field does not mean unlimited. The values may be blank only when `unlimited` is ticked (a database constraint and an admin error).
- Callers read the limits through `ServiceAccount.in_flight_cap` and `ServiceAccount.rate_limit`. Both return `None` only for an unlimited account. `rate_limit` joins the count and period into the text Django REST framework's throttles read, e.g. `100/day`. If a value is missing without `unlimited`, they raise `IncompleteLimitsError`, so whatever enforces the limit must deny the request, never treat it as unlimited. A new "tier" is an admin edit, not a code change.
- This change only stores the limits. Enforcing them is separate work.

## Consequences

- Making an account unlimited takes a deliberate tick of "Service Account has no limits"; clearing a field is an error, not unlimited.
- Whatever enforces the limits must use `in_flight_cap` and `rate_limit`, and deny the request if they raise `IncompleteLimitsError`.
- Saving an account in the admin writes only the fields that changed, so it can't overwrite a webhook secret regenerated at the same time.
- The migration gives every existing account the defaults in force when `migrate` runs (10 at once, 100 per day unless `DEFAULT_MAX_IN_FLIGHT` or `DEFAULT_RATE_LIMIT_COUNT` are set). Changing them later only affects new accounts. Nothing enforces them yet, so nothing changes today. Before enforcement ships, review each existing account's limits (or set the live sites' accounts deliberately), or they will be capped by the defaults.
