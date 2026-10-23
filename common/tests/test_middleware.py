from unittest.mock import MagicMock

from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings
from parameterized import parameterized

from common.middleware import HEADER_META_KEY, OriginLockMiddleware


class OriginLockMiddlewareTests(SimpleTestCase):
    def setUp(self) -> None:
        self.get_response = MagicMock(return_value=HttpResponse("ok"))
        self.middleware = OriginLockMiddleware(self.get_response)

    def _call(self, **meta):
        return self.middleware(RequestFactory().get("/api/", **meta))

    @parameterized.expand(
        [
            ("no_header", {}),
            ("wrong_secret", {HEADER_META_KEY: "nope"}),
            ("empty_secret", {HEADER_META_KEY: ""}),
        ]
    )
    @override_settings(ORIGIN_LOCK_SECRET="s3cret")
    def test_rejects_requests_without_the_secret(self, _name, meta) -> None:
        response = self._call(**meta)

        self.assertEqual(response.status_code, 403)
        self.get_response.assert_not_called()

    @override_settings(ORIGIN_LOCK_SECRET="s3cret")
    def test_passes_requests_with_the_secret(self) -> None:
        response = self._call(**{HEADER_META_KEY: "s3cret"})

        self.assertEqual(response.status_code, 200)
        self.get_response.assert_called_once()

    @override_settings(ORIGIN_LOCK_SECRET="")
    def test_does_nothing_when_no_secret_is_configured(self) -> None:
        response = self._call()

        self.assertEqual(response.status_code, 200)
        self.get_response.assert_called_once()

    @override_settings(ORIGIN_LOCK_SECRET="sécret")
    def test_handles_non_ascii_secrets(self) -> None:
        self.assertEqual(self._call(**{HEADER_META_KEY: "sécret"}).status_code, 200)
        self.assertEqual(self._call(**{HEADER_META_KEY: "secret"}).status_code, 403)
