# Production setup (Terraform)

Builds the prod Google Cloud setup from files, instead of clicking through the Console. Draft: not yet applied. The staging guide ([deploying-staging.md](../../docs/deploying-staging.md)) explains what each piece is.

**Made by Terraform:** project (optional), services, the two service accounts, image repository, empty secrets, the app, the migrations job, the worker job (with OCR), and the worker's schedule.

**Still by hand:** the Neon database, the R2 buckets, secret values, copying images in from staging, the Cloud Build deploy trigger, Cloudflare, the admin login ([Admin guide](../../docs/admin-guide.md)).

## One-time setup

1. Install [Terraform](https://developer.hashicorp.com/terraform/install) 1.10 or newer (OpenTofu 1.10+ also works).
2. In Cloudflare, make a private R2 bucket for Terraform's own notes (e.g. `dice-terraform-state`) and an API token for just that bucket.
3. In `terraform/prod/`: copy `backend.hcl.example` to `backend.hcl` and `terraform.tfvars.example` to `terraform.tfvars`, and fill them in. Neither is committed.
4. Log in to Google: `gcloud auth application-default login`.
5. Set the R2 token: `export AWS_ACCESS_KEY_ID=...` and `export AWS_SECRET_ACCESS_KEY=...`.
6. `terraform init -backend-config=backend.hcl`

R2 keeps no old versions of the state file. Copy `terraform.tfstate` somewhere safe after big changes.

## First apply (two passes)

Cloud Run won't start until its images and secrets exist, so:

**Pass 1** (`deploy_cloud_run = false`): `terraform plan`, read it, then `terraform apply`. This makes everything except Cloud Run.

Then, by hand:
- Add each secret's value: `printf '%s' 'VALUE' | gcloud secrets versions add dice-secret-key --data-file=-` (names are in `terraform output secret_names`).
- Put the app and OCR images in the new repository (`terraform output image_repository`): copy them from staging so prod runs exactly what was tested.

**Pass 2:** set `deploy_cloud_run = true` and both image names in `terraform.tfvars`, then `terraform plan` and `terraform apply`.

Last: **Execute** `dice-migrate`, then create the admin login and accounts.

## After that

Prod does not update on its own. Staging updates on every release; prod only changes when someone clicks a promote button (a Cloud Build trigger that needs approval, [DIC-2338](https://linear.app/dicelabs/issue/DIC-2338) and [DIC-2339](https://linear.app/dicelabs/issue/DIC-2339), not built yet). That button changes the app and worker images, and Terraform ignores image changes on purpose so it won't undo a promotion. Use Terraform for everything else (settings, sizes, schedule). A setting changed here takes effect on the next apply only.
