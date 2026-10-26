# Production setup (OpenTofu)

Builds the prod Google Cloud setup from files, instead of clicking through the Console.

We use [OpenTofu](https://opentofu.org), the open-source fork of Terraform. It reads the same files, so the folder, the `.tf` files and the `terraform.tfstate` name all stay as they are. Only the command is different: `tofu` instead of `terraform`.

The resources themselves live in [`../modules/dice`](../modules/dice), shared with staging. This folder holds prod's own state and settings.

**Made by OpenTofu:** project (optional), services, the Cloudflare Worker and its hostname, the service accounts (app and worker, deploys, and the nightly backup), image repository, empty secrets, the app, the migrations job, the worker job (with OCR), the nightly database backup job, both schedules, and the cleanup of old images (older than 30 days, keeping the newest 2).

**Still by hand:** the Neon database, the R2 buckets (documents, and a separate one for backups), secret values, copying images in from staging, the Cloud Build deploy trigger, Cloudflare, the Worker's secret ([Part 5](../../../docs/deploying-prod.md)), the admin login ([Admin guide](../../../docs/admin-guide.md)).

**Setup and deploying: [docs/deploying-prod.md](../../../docs/deploying-prod.md).** The staging guide ([deploying-staging.md](../../../docs/deploying-staging.md)) explains what each piece is.
