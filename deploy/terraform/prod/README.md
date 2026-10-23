# Production setup (OpenTofu)

Builds the prod Google Cloud setup from files, instead of clicking through the Console. Draft: not yet applied.

We use [OpenTofu](https://opentofu.org), the open-source fork of Terraform. It reads the same files, so the folder, the `.tf` files and the `terraform.tfstate` name all stay as they are. Only the command is different: `tofu` instead of `terraform`.

**Made by OpenTofu:** project (optional), services, the two service accounts, image repository, empty secrets, the app, the migrations job, the worker job (with OCR), and the worker's schedule.

**Still by hand:** the Neon database, the R2 buckets, secret values, copying images in from staging, the Cloud Build deploy trigger, Cloudflare, the admin login ([Admin guide](../../../docs/admin-guide.md)).

**Setup and deploying: [docs/deploying-prod.md](../../../docs/deploying-prod.md).** The staging guide ([deploying-staging.md](../../../docs/deploying-staging.md)) explains what each piece is.
