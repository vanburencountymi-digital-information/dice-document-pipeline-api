.PHONY: build up down migrate shell verapdf-version init

build:
	docker compose build

up:
	docker compose up

# First-time setup: build the image, apply migrations, then start the app.
# `up` blocks in the foreground, so migrate has to happen before it, not after.
init: build migrate up

down:
	docker compose down

migrate:
	docker compose run --rm app python manage.py migrate

shell:
	docker compose run --rm app /bin/sh

verapdf-version:
	docker compose run --rm app verapdf --version
