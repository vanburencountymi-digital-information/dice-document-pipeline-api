#!/usr/bin/env bash
# Fails if the Postgres client in the Dockerfile isn't the same major version as the database
# image in docker-compose.yml. The nightly backup's `pg_dump` must be at least as new as the
# server (DIC-2540). Run by pre-commit (.pre-commit-config.yaml).
set -euo pipefail

cd "$(dirname "$0")/../.."
client=$(grep -oE 'postgresql[0-9]+-client' Dockerfile | head -1 | grep -oE '[0-9]+' || true)
server=$(grep -oE 'image: postgres:[0-9]+' docker-compose.yml | head -1 | grep -oE '[0-9]+$' || true)

if [[ -z "$client" || -z "$server" ]]; then
  echo "Couldn't find both versions: Dockerfile's postgresql<N>-client and docker-compose.yml's postgres:<N>." >&2
  exit 1
fi
if [[ "$client" != "$server" ]]; then
  echo "Dockerfile installs postgresql$client-client but docker-compose.yml runs postgres:$server. Make them match." >&2
  exit 1
fi
