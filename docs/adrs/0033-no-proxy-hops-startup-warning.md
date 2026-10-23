# 33. No proxy-hops startup warning

## Status

Accepted. Amends [ADR 0031](0031-api-throttling.md).

## Context

ADR 0031 logged a warning at startup if `TRUSTED_PROXY_HOPS` was 0 outside local development. To know "outside local development" it read `DEBUG`, a second setting that has to be right for the warning to make sense. Security settings are now decided by `REQUIRE_HTTPS` ([ADR 0032](0032-browser-security-settings.md)), and `DEBUG` is only about error pages.

## Decision

- The startup warning is removed.
- `TRUSTED_PROXY_HOPS` keeps its default of 0, which ignores `X-Forwarded-For`, so an unconfigured host is still safe.
- Each deployment sets the real number: `1` on Cloud Run, in `terraform/prod/main.tf` and in the staging deploy guide.
- The rest of ADR 0031 is unchanged. The cost is that a deployment that forgets the setting is not warned, and its callers share one throttle counter.
