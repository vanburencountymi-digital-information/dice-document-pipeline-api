"""Settings for the test suite — `manage.py test --settings=config.test_settings`, wired
into `make test`.
"""

from unittest.mock import MagicMock

import sentry_sdk

sentry_sdk.init = MagicMock()  # type: ignore[misc]

from config.settings import *  # noqa: E402,F403

TASKS = {"default": {"BACKEND": "django.tasks.backends.immediate.ImmediateBackend"}}

# Tests run with DEBUG=False but never run `collectstatic`, so WhiteNoise's manifest storage
# would fail any test that renders a template using {% static %} (e.g. admin pages).
STORAGES = {
    **STORAGES,  # noqa: F405
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
