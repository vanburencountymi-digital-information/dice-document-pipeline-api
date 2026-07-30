# Project instructions

## Documentation
- If asked to generate documentation, please make it terse (1/2 to 1/3 the length you normally would). For non ADR documents, use a minimum of jargon, instead focusing on plain English and general programming concepts where applicable. Assume the end-users for this repository may have varying levels of programming knowledge, and may include government workers attempting to use open source solutions with very little to no previous engineering background.

- Whenever an ADR is added, renamed, or its Decision materially changes in `docs/adrs/`, update `docs/adrs/0000-README.md` in the same change: add/update its numbered entry, link, and one-sentence decision summary. Do this without being asked.

- As a point of terminology, this repo prefers slim models, service classes to interact with models, and slim views. Service classes that interact directly with models are called "-Service", service classes for outside packages are "-Adapter", and service classes for outside APIs are "-Client".

## Formatting
- This repo uses ruff + mypy for formatting + linting. Allow the auto-linter to handle line-length.

## Testing
If asked to create a test, observe the following guidelines:

- Each Django module that will have tests in it will have a `/tests/` folder. Factories should go in this folder, in `factories.py`. Any non-factory helpers should be in `helpers.py`, but these should be minimal - prefer Factory generation whenever possible.
- Tests files should be named after the exact module they test, so a `services.py` file's tests should be in `test_services.py` in the same module.
- We prefer tests with clear boundaries. In the case of a view that calls a service class to perform an operation, the unit tests in `test_views.py` should mock the service class (ideally with a factory if needed) using the unittest.mock library (prefer the `@patch` decorator when possible, and with `auto_spec` and `spec_set` when possible/applicable.)
- Use `@parameterized.expand` decorator when possible to condense tests.
- Use `SimpleTestCase` for tests that don't require the DB if possible, `setUpTestData` (over `setUp`) when possible.
- Tests should work the same on both local + production environments; for tasks, use `override_settings` and mock the task backend with `django.tasks.backends.immediate.ImmediateBackend`
