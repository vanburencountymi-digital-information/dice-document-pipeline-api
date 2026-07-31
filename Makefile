.PHONY: build up down recreate migrate shell pyshell test verapdf-version init

build:
	# Docker auto-creates a missing bind-mount source as root, which then blocks
	# the app container's non-root user from writing to it — pre-create it
	# ourselves so it's always owned by whoever runs `make`.
	mkdir -p media
	docker compose build

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

shell:
	docker compose run --rm app /bin/sh

pyshell:
	docker compose run --rm app python manage.py shell

test:
	docker compose run --rm app python manage.py test

verapdf-version:
	docker compose run --rm app verapdf --version
