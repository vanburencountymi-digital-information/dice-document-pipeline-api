# Dice Document Pipeline API

A Django API that takes an uploaded PDF, checks it against accessibility standards (WCAG 2.1 AA / PDF/UA), and — if it fails — runs it through an automated remediation pipeline (OCR, tagging, alt text, link repair) before re-checking it. This is a rebuild of an earlier prototype (see History below); the current architecture and status live in [`implementation_plan.md`](implementation_plan.md) and [`docs/adrs/`](docs/adrs/0000-README.md).

## Setup

### 1. Install the git hooks (either path below)

Linting and type checking (ruff + mypy) run automatically on commit via pre-commit. This runs on your host machine's `git commit`, not inside Docker, so it's needed regardless of which path you pick below:

```bash
pipx install pre-commit   # or: pip install pre-commit
pre-commit install
```

### 2a. Docker (recommended)

Includes Postgres and the veraPDF/Java tooling the precheck/postcheck stages need — see [`docs/adrs/0007-java-version.md`](docs/adrs/0007-java-version.md).

```bash
cp .env.example .env   # then set SECRET_KEY (see comment in the file)
make init              # first time only: build the image, migrate, then start the app
```

The app runs at `http://localhost:8000`. Other commands:

```bash
make build            # rebuild the image (after changing requirements.txt/Dockerfile)
make migrate          # apply new migrations to the running Postgres
make up               # start the app (without rebuilding or migrating)
make down             # stop everything
make shell            # shell inside the app container
make test             # run the test suite
make verapdf-version  # confirm veraPDF/Java installed correctly
```

### 2b. Bare-metal (Python only)

Faster to start, but falls back to SQLite and won't have veraPDF/Java available — fine for API/model work, not for testing the precheck/postcheck stages.

```bash
python3.12 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then set SECRET_KEY
python manage.py migrate
python manage.py runserver
```

## Problem Definition

In the first half of 2026, Jerry Happel and Drake Olejniczak from the [Van Buren County Digital Information Department](https://vanburencountymi.gov/departments/departments-offices/digital-information/) were facing an interesting and pressing problem: the [Van Buren County website](https://vanburencountymi.gov) contained almost 4000 media files, including thousands of PDF files that would not meet the WCAG 2.1 Level AA technical standard required by ADA Title II web and mobile accessibility compliance.

## History: the v1 prototype

Their first answer, [Dice Document Pipeline](https://github.com/vanburencountymi-digital-information/dice-document-pipeline), proved AI-assisted remediation could work at high quality: it drove Adobe Acrobat Pro through COM automation to OCR and auto-tag PDFs on a Windows desktop, then ran a Python pass for Claude-assisted alt text and compliance scoring. It batch-remediated over a thousand documents successfully, but needed a Windows desktop with Acrobat installed and a person to manually scrape, download, and re-upload documents — not something that scales to an ongoing pipeline.

This repo is a from-scratch rebuild as a proper API: Django + DRF, no Windows/Acrobat dependency, engine choices re-evaluated rather than inherited from v1 (see the ADRs linked above for what was kept, what changed, and why).
