# 40. One OpenTofu module for staging and prod

## Status

Accepted. Amends [ADR 0022](0022-automated-staging-deploys.md) — that ADR is not rewritten; this one records the change. It says the Cloud Run app and jobs are "created once by hand"; staging is now described in files, like prod.

## Context

Prod's Google Cloud setup was written as OpenTofu files (DIC-2329). Staging was still built by hand in the Console, so the two could drift apart, and staging couldn't test the setup prod uses (DIC-2561).

## Decision

- Both environments use one module, `deploy/terraform/modules/dice`. `prod/` and `staging/` are thin folders that call it. Each has its own settings file and its own state file, kept in its own R2 bucket, so a mistake in one can't touch the other.
- What differs is a module input. Each default keeps prod as it was: the environment name (service account names), the Sentry tag, the nightly backup on or off, the origin lock on or off, whether the worker's schedule is paused, and the worker and OCR sizes.
- Staging turns the backup off ([ADR 0029](0029-staging-worker-paused-by-default.md)) and the origin lock off (no Cloudflare), keeps the schedule paused, and has no custom domain. Without one, the app's hostname is the `run.app` address Cloud Run gives every service. It is worked out from the project number, so the first apply has it right.
- Prod and staging each set their own worker and OCR sizes, so changing a module default can't change either one.
- Prod was moved into the module with `moved` blocks, so its existing state followed the code. Before and after plans matched exactly. Staging was adopted with `import` blocks. Both are one-time and can be deleted once applied.
- Still by hand: the Neon databases, the R2 buckets, secret values, the Cloud Build triggers and the first admin login. Cloud Build keeps changing images on each deploy, so OpenTofu ignores them.

## Consequences

- A change to the module affects both environments. Try it on staging first, and run `tofu plan` for prod before applying.
- Staging's first plan may show small fixes to what was set up by hand (a missing setting, a schedule that differed from the docs). Read it before applying.
- Staging's public address has no Cloudflare or other deployment-level limit in front of it, only the app's own throttling. Accepted, because staging holds no real documents.
- The project resource keeps the name `prod` inside the module, because prod's state already knows it by that name.
