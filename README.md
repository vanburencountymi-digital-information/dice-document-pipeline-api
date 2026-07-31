# Dice Document Pipeline API

A Django API that takes an uploaded PDF, checks it against accessibility standards (WCAG 2.1 AA / PDF/UA). Upon failure, it runs the document through an automated remediation pipeline (OCR, tagging, metadata fixes, alt text, link repair) before re-checking it. This is a rebuild of an earlier prototype (see History below).

## Setup

Docker only — the current implementation depends on Postgres, Java/veraPDF, and the OpenDataLoader hybrid server.

### 1. Docker

Includes Postgres, the veraPDF/Java tooling the precheck/postcheck stages need (see [`docs/adrs/0007-java-version.md`](docs/adrs/0007-java-version.md)), and the OpenDataLoader hybrid server the `ocr` stage needs (see [`docs/adrs/0004-ocr-tagging-engine.md`](docs/adrs/0004-ocr-tagging-engine.md)).

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

Not a `make` target, on purpose — generating a migration writes a new file that has to survive past the container, and the app service doesn't bind-mount its source (only `./media`, for inspecting remediated files). Run it with an explicit mount so the file lands in your actual `remediation/migrations/` (or whichever app's) directory, and `--user` so it's owned by you instead of the container's root:

```bash
docker compose run --rm --user "$(id -u):$(id -g)" -v "$(pwd):/app" app python manage.py makemigrations
```

now run `make build` and `make migrate`

### 2. Install the git hooks (if making changes)

Linting and type checking (ruff + mypy) run automatically on commit via pre-commit. This runs on your host machine's `git commit`, not inside Docker:

```bash
pipx install pre-commit   # or: pip install pre-commit
pre-commit install
```

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
3. Body: Choose `form-data` radio button. Add key `file`, change its type (in next colument) from "Text" to `File`, and pick a PDF.
4. Send. The response is the remediation job — `status` will be `COMPLETE` or `FAILED` immediately, since the pipeline runs synchronously by default.

The response will contain an `id` field with the remediation job id and a `document_id` that is a hashed key for your document; save this if you want to check status later.

#### Check a submitted document's status
1. New request: `GET http://localhost:8000/api/document-status/<document_id>/`, using the `document_id` from the submit response.
2. Same `Authorization` header as above.

---- Notes for later, ignore for now----

## Historical Data

## Problem Definition

In the first half of 2026, Jerry Happel and Drake Olejniczak from the [Van Buren County Digital Information Department](https://vanburencountymi.gov/departments/departments-offices/digital-information/) were facing an interesting and pressing problem: the [Van Buren County website](https://vanburencountymi.gov) contained almost 4000 media files, including thousands of PDF files that would not meet the WCAG 2.1 Level AA technical standard required by ADA Title II web and mobile accessibility compliance.

## History: the v1 prototype

Their first answer, [Dice Document Pipeline](https://github.com/vanburencountymi-digital-information/dice-document-pipeline), proved AI-assisted remediation could work at high quality: it drove Adobe Acrobat Pro through COM automation to OCR and auto-tag PDFs on a Windows desktop, then ran a Python pass for Claude-assisted alt text and compliance scoring. It batch-remediated over a thousand documents successfully, but needed a Windows desktop with Acrobat installed and a person to manually scrape, download, and re-upload documents — not something that scales to an ongoing pipeline.

This repo is a from-scratch rebuild as a proper API: Django + DRF, no Windows/Acrobat dependency, engine choices re-evaluated rather than inherited from v1 (see the ADRs linked above for what was kept, what changed, and why).
