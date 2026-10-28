.PHONY: build up down recreate migrate migrations shell pyshell test check-ocr-regression pdf-quality-report pdf-quality-compare check-pdf-quality verapdf-version init garage

build:
	# Docker auto-creates a missing bind-mount source as root, which then blocks
	# the app container's non-root user from writing to it — pre-create it
	# ourselves so it's always owned by whoever runs `make`.
	mkdir -p media
	# ADR 0012 — bake the nearest git tag into the image as PIPELINE_VERSION.
	# The fallback only matters before this repo's very first tagged release.
	docker compose build --build-arg PIPELINE_VERSION=$$(git describe --tags --abbrev=0 2>/dev/null || echo 0.0.0)

up:
	docker compose up

# First-time setup: build the image, apply migrations, then start the app.
# `up` blocks in the foreground, so migrate has to happen before it, not after.
init: build migrate up

down:
	docker compose down

# Docker only reads .env at container creation — use this after editing it
# (e.g. flipping a RUN_* pipeline toggle) so the change actually takes effect.
recreate: down build up

# Optional (ADR 0018) — pulls the pinned Garage image and (re)starts it fresh. Named
# volumes (garage_meta/garage_data) persist across this, so your cluster layout/bucket/
# key setup isn't wiped. See readme for 1st time garage config
garage:
	docker compose pull garage
	docker compose up -d --force-recreate garage

migrate:
	docker compose run --rm app python manage.py migrate

# Unlike the other targets, this needs its own volume mount + user override:
# the app image doesn't live-mount source (see `build`), so without `-v` this
# would run makemigrations against the last-built image, not your current
# models.py. `--user` keeps the generated file owned by you instead of the
# image's baked-in uid 1000, for hosts where that doesn't already match.
# Run `make build` and `make migrate` afterward to pick it up.
migrations:
	docker compose run --rm --user "$$(id -u):$$(id -g)" -v "$$(pwd):/app" app python manage.py makemigrations

shell:
	docker compose run --rm app /bin/sh

pyshell:
	docker compose run --rm app python manage.py shell

test:
	docker compose run --rm app python manage.py test --settings=config.test_settings

# Not part of `make test`/CI — needs the real opendataloader-hybrid container and is meant
# to be run by hand before/after bumping torch, the Docling fork, or opendataloader-pdf.
# See README.md's "Dependency Upgrades" section. Pass ARGS=--record to (re)generate the
# committed baseline instead of comparing against it.
check-ocr-regression:
	docker compose run --rm app python manage.py check_ocr_regression $(ARGS)

# Like check-ocr-regression, by hand around a dependency bump. Report: ARGS="--run-pipeline
# --output-dir /tmp/reports/new path/to/original.pdf". Compare: ARGS="/tmp/reports/old /tmp/reports/new".
pdf-quality-report:
	docker compose run --rm app python manage.py pdf_quality_report $(ARGS)

pdf-quality-compare:
	docker compose run --rm app python manage.py pdf_quality_compare $(ARGS)

# The whole battery in one go: runs the real pipeline on the committed slices, scores the
# text against the page readings, and compares with the recorded baselines. Fails if
# anything got worse. Needs the opendataloader-hybrid container; not part of `make test`.
check-pdf-quality:
	docker compose run --rm app sh -c 'python manage.py pdf_quality_report remediation/tests/fixtures/edge-cases/boc1020-*.pdf --run-pipeline --truth-dir remediation/tests/fixtures/ocr_truth --output-dir /tmp/quality && python manage.py pdf_quality_compare remediation/tests/fixtures/quality_baseline /tmp/quality --fail-on-regression'

verapdf-version:
	docker compose run --rm app verapdf --version
