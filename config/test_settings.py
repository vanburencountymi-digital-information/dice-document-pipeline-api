"""Settings for the test suite — `manage.py test --settings=config.test_settings`, wired
into `make test`.
"""

from unittest.mock import MagicMock

import sentry_sdk

sentry_sdk.init = MagicMock()  # type: ignore[misc]

from config.settings import *  # noqa: E402,F403
