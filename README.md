# Dice Document Pipeline API

A Django API that takes an uploaded PDF and checks it against accessibility standards (WCAG 2.1 AA / PDF/UA-1). Upon failure, it runs the document through an automated remediation pipeline (OCR, tagging, metadata fixes, alt text, link repair) before re-checking it.

## Setup

Docker only — the current implementation depends on Postgres, Java/veraPDF, and the OpenDataLoader hybrid server.

### 1. Docker

```bash
cp .env.example .env   # then set SECRET_KEY (see comment in the file)
```

Other make commands include:

```bash
make init             # first time only convenience method: build the images, migrate, then start the app
make build            # rebuild the images (needed after ANY app source change, not just requirements*.txt/Dockerfile* — the app image doesn't live-mount source)
make migrate          # apply migrations to the running Postgres
make up               # start the app (without rebuilding or migrating)
make down             # stop everything
make recreate         # a convenience method that bundles down, build, and up - use when you edit .env on an already-running container

make shell            # shell inside the app container
make pyshell          # Django shell (manage.py shell) inside the app container
make test             # run the test suite

make verapdf-version  # confirm veraPDF/Java installed correctly
```

The app runs at `http://localhost:8000`.

### Adding a migration

```bash
docker compose run --rm --user "$(id -u):$(id -g)" -v "$(pwd):/app" app python manage.py makemigrations
```
now run `make build` and `make migrate`

### 2. Install the git hooks (if making changes)

Linting and type checking (ruff + mypy) run automatically on commit via pre-commit, and commit messages are checked against [Conventional Commits](https://www.conventionalcommits.org/) (`fix:`/`feat:`/etc. — see ADR 0012, this is what drives automatic version tagging).

Both run on your host machine's `git commit`, not inside Docker:

```bash
pip install pre-commit
pre-commit install --hook-type pre-commit --hook-type commit-msg
```

## Pipeline steps

If all steps are enabled via environment variable, a document upload runs through the following steps in order (see [ADR 0003](docs/adrs/0003-pipeline-steps-and-branching.md) and [ADR 0010](docs/adrs/0010-fix-opendataloader-hybrid-tagging-upstream.md)):

1. `PrecheckService` → Verify with veraPDF
2. `OCRService` → OCR + build tagged PDF via OpenDataLoader
3. `FontRepairService` → Repair font `ToUnicode`/`CIDSet` (pikepdf)
4. `MetadataService` → Fix metadata (pikepdf) — `MarkInfo`/`Lang`/title/tab-order
5. `LinkService` → Tag links (pikepdf)
6. `AltTextService` → Alt text (Claude Vision)
7. `ScoringService` → Heuristic compliance score, non-blocking (pikepdf/PyMuPDF)
8. `PostCheckService` → Verify with veraPDF


## Testing the API

The API authenticates requests with a token tied to a `ServiceAccount`. To create one, open a Django shell (`make pyshell`) and run:

```python
from accounts.models import Organization
from accounts.services import ServiceAccountService

org = Organization.objects.create(name="Test Org")
account = ServiceAccountService().create(org, "test-service-account")
print(account.token)  # save this
```

### With Postman

#### Submit a document

1. New request: `POST http://localhost:8000/api/submit-document/` (or whatever URL you've deployed to)
2. Headers: Key: `Authorization`, Value: `Token <the token you printed above>`
3. Body: Choose `form-data` radio button. Add key `file`, change its type (in next column) from "Text" to `File`, and pick a PDF.
4. Send. The response is the remediation job — `status` will be `COMPLETE` or `FAILED` immediately, since the pipeline runs synchronously when deployed locally (via Django Tasks ImmediateBackend.)

The response will contain an `id` field with the remediation job id and a `document_id` that is a hashed key for your document; save this if you want to check status later.

#### Resubmit a document

A document that has been processed by the pipeline is hashed, so that future attempts of the same file by the same organization return the previous result without re-running the whole pipeline.

If you would like to force re-running the pipeline, such as during testing, click the radio button for `form-data` and next to the `file` key, add a second key, `force`, type Text, value `true`.

#### Check a submitted document's status

Submissions in the local environment process synchronously (and therefore don't return a response to postman until the document has finished processing). However, there is a known bug with postman where a long running POST waiting for a response (as would occur when OCR'ing a 150 page, complex document) can appear to hang--in other words, the POST never appears to return a response to PostMan, even though the job actually completed. If you suspect this may have happened (i.e., job running over an hour), you can check via the `document-status` endpoint:

1. New request: `GET http://localhost:8000/api/document-status/<document_id>/`, using the `document_id` from the submit response.
2. Same `Authorization` header as above.

You should see `status` in the response. If the status is `running`, the job is still in progress.

## Dependency Upgrades

### Setting a Retry Floor

By default, re-running the same document from the same organization returns the result from the previous run unless `force=True` has been passed in with the `POST`.

However, upgrading a major dependency might mean that jobs that previously failed would now pass with the current pipeline. You can set the retry floor to
any version of the pipeline that has previously run a remediation job via the admin panel. If a file from a previously failed job that was run on a version of the pipeline older than the floor is sent by the same organization, it will automatically be retried to see if the version change has corrected whatever previously caused the file to fail.

### OpenDataloader and Docling

This repo is currently pinned to forked versions of OpenDataLoader and Docling due to the need for bug-fixes not present in the original files. We periodically check to see if the main images have been upgraded to include those bugfixes; once they have, we will pin to main branch.

### VeraPDF

This repo currently validates against PDF/UA-1, not the newer PDF/UA-2, because PDF/UA-2 targets PDF 2.0's structure model and none of the available tooling (OpenDataLoader, pikepdf) produces PDF 2.0 output yet.

If the veraPDF version in the `Dockerfile` is ever upgraded, re-run `manage.py extract_verapdf_profile --jar-path <path to the new cli jar>` to refresh `remediation/adapters/verification/pdfua1_catalog.json`, then review `remediation/adapters/verification/severity.py`'s severity tables against whatever changed before bumping `BUILT_AGAINST_VERAPDF_VERSION`.


---- Notes for later, ignore for now----

## Historical Data

## Problem Definition

In the first half of 2026, Jerry Happel and Drake Olejniczak from the [Van Buren County Digital Information Department](https://vanburencountymi.gov/departments/departments-offices/digital-information/) were facing an interesting and pressing problem: the [Van Buren County website](https://vanburencountymi.gov) contained almost 4000 media files, including thousands of PDF files that would not meet the WCAG 2.1 Level AA technical standard required by ADA Title II web and mobile accessibility compliance.

## History: the v1 prototype

Their first answer, [Dice Document Pipeline](https://github.com/vanburencountymi-digital-information/dice-document-pipeline), proved AI-assisted remediation could work at high quality: it drove Adobe Acrobat Pro through COM automation to OCR and auto-tag PDFs on a Windows desktop, then ran a Python pass for Claude-assisted alt text and compliance scoring. It batch-remediated over a thousand documents successfully, but needed a Windows desktop with Acrobat installed and a person to manually scrape, download, and re-upload documents — not something that scales to an ongoing pipeline.

This repo is a from-scratch rebuild as a proper API: Django + DRF, no Windows/Acrobat dependency, engine choices re-evaluated rather than inherited from v1 (see the ADRs linked above for what was kept, what changed, and why).
