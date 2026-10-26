# Staging setup (OpenTofu)

Describes the staging Google Cloud setup in files, using the same shared code as [prod](../prod/README.md) ([`../modules/dice`](../modules/dice)). It has its own state, in its own R2 bucket, so a staging mistake can't touch prod.

**Different from prod:** no custom domain (the app uses its `run.app` address), no nightly backup, no origin lock, and the worker's schedule stays paused. Staging has its own R2 buckets (state and documents); none are shared with prod. It is public through Cloud Run's "invoker" setting, because our organization policy blocks the `allUsers` grant prod uses. With no Cloudflare in front, only the app's own throttling limits traffic (accepted, since staging holds no real documents).

**Still by hand:** the Neon database, the R2 buckets, secret values, the Cloud Build triggers, the admin login ([deploying-staging.md](../../../docs/deploying-staging.md)).

**To change something:** edit the files, then `tofu plan` and read it before any `tofu apply`. It should show no destroys.

**First time on a new machine:** copy `backend.hcl.example` and `terraform.tfvars.example` (neither is committed) and fill them in. Log in to Google (`gcloud auth application-default login`), set the R2 token for staging's state bucket (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`), then `tofu init -backend-config=backend.hcl`.

Staging was built by hand first, then adopted into OpenTofu ([ADR 0040](../../../docs/adrs/0040-shared-opentofu-module-for-staging-and-prod.md)).
