# 12. Automatic semantic versioning via Conventional Commits

## Status

Accepted

## Context

Two later decisions — versioned retry ([ADR 0014](0014-versioned-retry.md)) and a retry-floor admin control — need a version to exist on every `Remediation` row, so a resubmitted `FAILED` document can be compared against "is this worth retrying under newer pipeline logic."

A hand-bumped constant in a settings file was considered and rejected: it's one more thing a change can forget to update, and it duplicates information git already has (a tag). Tying the version to *every* commit was also considered and rejected on its own — the retry-floor dropdown is more useful with a handful of meaningful versions to pick from than one per trivial fix. That concern turned out not to matter in practice, though: since raising the retry floor is always a deliberate, manual admin action (never automatic), it doesn't cost anything for the version number itself to move often — a human still chooses when a version is actually worth retrying over.

## Decision

Adopt [Conventional Commits](https://www.conventionalcommits.org/) (`fix:`→patch, `feat:`→minor, `feat!:`/`BREAKING CHANGE:`→major, `chore:`/`docs:`/`test:`/`ci:`→no bump) and automate the release itself. Add a new `.github/workflows/release.yml` running `python-semantic-release` on every push to `main`. The git tag it creates (`vX.Y.Z`) is the sole source of truth: no version string is written into any file in the repo (`version_toml`/`version_variables` deliberately left unset in `pyproject.toml`'s `[tool.semantic_release]`).

Commit message format is enforced locally before it can reach GitHub — a new `conventional-pre-commit` hook in `.pre-commit-config.yaml`, at the `commit-msg` stage (requires `pre-commit install --hook-type commit-msg` in addition to the existing default stage install — see the updated README).

The running app reads its own version from the nearest git tag at Docker build time, not from CI: `Makefile`'s `build` target computes `git describe --tags --abbrev=0` on the host and passes it to `docker compose build --build-arg PIPELINE_VERSION=...`; the `Dockerfile` bakes it in as a runtime `ENV`; `config/settings.py` reads it (`PIPELINE_VERSION = env.str("PIPELINE_VERSION", default="0.0.0")` — the fallback only matters before this repo's first tagged release ever ships). This means both a CI-built image and a developer's local `make build` see the same version consistently, with no separate "how does the running app learn the version" mechanism to keep in sync.

`Remediation.pipeline_version` stamps `settings.PIPELINE_VERSION` at creation time (`RemediationService.create()`). A new `PipelineConfig` singleton model holds `retry_floor_version` (admin-editable, dropdown populated from `Remediation.objects.values_list("pipeline_version", flat=True).distinct()` — versions that have actually produced a real attempt, not a live query against GitHub's tag list) — consumed starting in ADR 0014. This step is otherwise inert: nothing's behavior changes yet.

## Consequences

- A malformed commit message is rejected locally, before push — not just after a CI run fails.
- The version number will bump on essentially every merge to `main` (even a trivial `fix:`).
- No file in the repo (e.g. `pyproject.toml`'s `[project.version]`) reflects the current version — only `git tag` and the running app's `PIPELINE_VERSION` env var do. A developer checking "what version is this" needs `git describe --tags` or to ask the running app, not to open a file.
- No bulk/proactive retry mechanism exists yet from this step alone — see [ADR 0014](0014-versioned-retry.md) for what actually consumes `pipeline_version`/`retry_floor_version`.
