.PHONY: build up down recreate migrate migrations shell pyshell test verapdf-version init

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
	docker compose run --rm app python manage.py test

verapdf-version:
	docker compose run --rm app verapdf --version
