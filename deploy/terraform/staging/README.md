# Staging setup (OpenTofu)

Describes the staging Google Cloud setup in files, using the same shared code as [prod](../prod/README.md) ([`../modules/dice`](../modules/dice)). It has its own state, so a staging mistake can't touch prod.

**Different from prod:** no custom domain (the app uses its `run.app` address), no nightly backup, no origin lock, and the worker's schedule stays paused. Staging has its own R2 buckets (state and documents); none are shared with prod. Its public `run.app` address has no Cloudflare in front, so only the app's own throttling limits traffic (accepted, since staging holds no real documents).

**Still by hand:** the Neon database, the R2 buckets, secret values, the Cloud Build triggers, the admin login ([deploying-staging.md](../../../docs/deploying-staging.md)).

**First time:** staging already exists, so `imports.tf` adopts what's there. Copy `backend.hcl.example` and `terraform.tfvars.example` (neither is committed), run `tofu init -backend-config=backend.hcl`, then `tofu plan`. Read the plan before any `apply`: it should show no destroys, and only small, explainable changes.
