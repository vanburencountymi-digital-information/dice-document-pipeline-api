# Production setup (OpenTofu)

Builds the prod Google Cloud setup from files, instead of clicking through the Console. Draft: not yet applied.

We use [OpenTofu](https://opentofu.org), the open-source fork of Terraform. It reads the same files, so the folder, the `.tf` files and the `terraform.tfstate` name all stay as they are. Only the command is different: `tofu` instead of `terraform`.

The resources themselves live in [`../modules/dice`](../modules/dice), shared with other environments. This folder holds prod's own state, settings and the `moved` blocks that keep its existing state matched to the module.

**Made by OpenTofu:** project (optional), services, the service accounts (app and worker, deploys, and the nightly backup), image repository, empty secrets, the app, the migrations job, the worker job (with OCR), the nightly database backup job, and both schedules.

**Still by hand:** the Neon database, the R2 buckets (documents, and a separate one for backups), secret values, copying images in from staging, the Cloud Build deploy trigger, Cloudflare, the admin login ([Admin guide](../../../docs/admin-guide.md)).

**Setup and deploying: [docs/deploying-prod.md](../../../docs/deploying-prod.md).** The staging guide ([deploying-staging.md](../../../docs/deploying-staging.md)) explains what each piece is.
