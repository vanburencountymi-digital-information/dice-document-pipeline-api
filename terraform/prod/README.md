# Production setup (OpenTofu)

Builds the prod Google Cloud setup from files, instead of clicking through the Console. Draft: not yet applied. The staging guide ([deploying-staging.md](../../docs/deploying-staging.md)) explains what each piece is.

We use [OpenTofu](https://opentofu.org), the open-source fork of Terraform. It reads the same files, so the folder, the `.tf` files and the `terraform.tfstate` name all stay as they are. Only the command is different: `tofu` instead of `terraform`.

**Made by OpenTofu:** project (optional), services, the two service accounts, image repository, empty secrets, the app, the migrations job, the worker job (with OCR), and the worker's schedule.

**Still by hand:** the Neon database, the R2 buckets, secret values, copying images in from staging, the Cloud Build deploy trigger, Cloudflare, the admin login ([Admin guide](../../docs/admin-guide.md)).

## One-time setup

1. Install OpenTofu 1.10 or newer ([all install options](https://opentofu.org/docs/intro/install/)). On Ubuntu or Debian (including WSL):

   ```bash
   curl --proto '=https' --tlsv1.2 -fsSL https://get.opentofu.org/install-opentofu.sh -o install-opentofu.sh
   chmod +x install-opentofu.sh
   ./install-opentofu.sh --install-method deb
   rm install-opentofu.sh
   tofu version
   ```

   On a Mac: `brew install opentofu`. `tofu version` must show 1.10 or higher.
2. In Cloudflare, make a private R2 bucket for OpenTofu's own notes (e.g. `dice-terraform-state`) and an API token for just that bucket.
3. In `terraform/prod/`: copy `backend.hcl.example` to `backend.hcl` and `terraform.tfvars.example` to `terraform.tfvars`, and fill them in. Neither is committed.
4. Log in to Google: `gcloud auth application-default login`.
5. Set the R2 token: `export AWS_ACCESS_KEY_ID=...` and `export AWS_SECRET_ACCESS_KEY=...`.
6. `tofu init -backend-config=backend.hcl`

R2 keeps no old versions of the state file. Copy `terraform.tfstate` somewhere safe after big changes.

## First apply (two passes)

Cloud Run won't start until its images and secrets exist, so:

**Pass 1** (`deploy_cloud_run = false`): `tofu plan`, read it, then `tofu apply`. This makes everything except Cloud Run.

Then, by hand (you can also set them in cloud secrets manager)
- Add each secret's value: `printf '%s' 'VALUE' | gcloud secrets versions add dice-secret-key --data-file=-` (names are in `tofu output secret_names`).
- Put the app and OCR images in the new repository (`tofu output image_repository`): copy them from staging so prod runs exactly what was tested. See below.

### Copying the images

Use **Cloud Shell** (the `>_` button in the Google Cloud Console): the OCR image is about 12 GB, and copying inside Google's network is much faster than through your computer.

1. Find what staging runs now (the app image tag is a version like `v5.4.0`; the OCR tag is a short code):

   ```bash
   gcloud run services describe dice-app --region us-central1 \
     --project ada-document-pipeline-staging \
     --format='value(spec.template.spec.containers[0].image)'
   gcloud run jobs describe dice-worker --region us-central1 \
     --project ada-document-pipeline-staging --format=export | grep image:
   ```

2. Install `crane`, a small tool that copies images between repositories. It stays in your Cloud Shell home folder, so this is a one-time step:

   ```bash
   mkdir -p ~/bin
   curl -sL https://github.com/google/go-containerregistry/releases/latest/download/go-containerregistry_Linux_x86_64.tar.gz | tar -xz -C ~/bin crane
   export PATH=$PATH:~/bin
   gcloud auth configure-docker us-central1-docker.pkg.dev
   ```

3. Copy both images, using the tags from step 1:

   ```bash
   S=us-central1-docker.pkg.dev/ada-document-pipeline-staging/dice
   D=us-central1-docker.pkg.dev/ada-document-pipeline-prod/dice
   crane copy $S/app:vX.Y.Z $D/app:vX.Y.Z
   crane copy $S/opendataloader-hybrid:TAG $D/opendataloader-hybrid:TAG
   ```

4. In the Console, open **Artifact Registry → dice** in the prod project and check both tags are there.

### Allow public access

Our organization blocks making a service public by default, so `tofu apply` fails on `dice-app` with "users named in the policy do not belong to a permitted customer". The API still needs its own tokens; this only lets requests reach the app. Switch the block off for the prod project only. You need the **Organization Policy Administrator** role on the organization (ask an org admin if you don't have it):

```bash
gcloud org-policies set-policy policy.yaml
```

Run it from this folder (`policy.yaml` is here). Or in the Console: **IAM & Admin → Organization Policies → Domain restricted sharing → Manage policy → Override parent's policy**, add a rule with enforcement **Off**, then **Set policy**. Wait a few minutes before applying.

**Pass 2:** set `deploy_cloud_run = true` and both image names in `terraform.tfvars`, using the **prod** repository path (`…/ada-document-pipeline-prod/dice/app:vX.Y.Z`, not staging's). Then `tofu plan` and `tofu apply`.

Last: **Execute** `dice-migrate`, then create the admin login and accounts.

## After that

Prod does not update on its own. Staging updates on every release; prod only changes when someone clicks a promote button (a Cloud Build trigger that needs approval, [DIC-2338](https://linear.app/dicelabs/issue/DIC-2338) and [DIC-2339](https://linear.app/dicelabs/issue/DIC-2339), not built yet). That button changes the app and worker images, and OpenTofu ignores image changes on purpose so it won't undo a promotion. Use OpenTofu for everything else (settings, sizes, schedule). A setting changed here takes effect on the next apply only.
