# 34. Cloudflare address ranges live in code

## Status

Accepted. Amends [ADR 0031](0031-api-throttling.md).

## Context

ADR 0031 made Cloudflare's address ranges (`CLOUDFLARE_IP_RANGES`) settable from the environment. They change roughly once a year, and changing an environment variable on Cloud Run is a new deployment anyway, so the override saved nothing.

## Decision

`CLOUDFLARE_IP_RANGES` is a plain list in `config/settings.py` and no longer reads an environment variable. A change goes through review and CI. The rest of ADR 0031 is unchanged, including how the list is used and that it should be compared against <https://www.cloudflare.com/ips/> from time to time.
