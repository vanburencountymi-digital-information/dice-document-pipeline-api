from pathlib import Path
from unittest.mock import MagicMock

from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings
from django.urls import reverse
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
    def test_logs_rejections_without_the_header_value(self) -> None:
        with self.assertLogs("common.middleware", level="WARNING") as logs:
            self._call(**{HEADER_META_KEY: "nope"})

        self.assertIn("GET '/api/'", logs.output[0])
        self.assertNotIn("nope", logs.output[0])

    @override_settings(ORIGIN_LOCK_SECRET="s3cret")
    def test_logged_path_cannot_forge_log_lines(self) -> None:
        request = RequestFactory().get("/%0aERROR%20fake")

        with self.assertLogs("common.middleware", level="WARNING") as logs:
            self.middleware(request)

        self.assertEqual(len(logs.output[0].splitlines()), 1)

    @override_settings(ORIGIN_LOCK_SECRET="s3cret")
    def test_passes_requests_with_the_secret(self) -> None:
        response = self._call(**{HEADER_META_KEY: "s3cret"})

        self.assertEqual(response.status_code, 200)
        self.get_response.assert_called_once()

    @parameterized.expand([("get", "get"), ("head", "head")])
    @override_settings(ORIGIN_LOCK_SECRET="s3cret")
    def test_lets_the_ping_url_through_without_the_secret(self, _name, method) -> None:
        response = self.middleware(getattr(RequestFactory(), method)("/ping/"))

        self.assertEqual(response.status_code, 200)
        self.get_response.assert_called_once()

    @parameterized.expand([("post", "post"), ("put", "put"), ("delete", "delete")])
    @override_settings(ORIGIN_LOCK_SECRET="s3cret")
    def test_still_locks_other_methods_on_the_ping_url(self, _name, method) -> None:
        response = self.middleware(getattr(RequestFactory(), method)("/ping/"))

        self.assertEqual(response.status_code, 403)
        self.get_response.assert_not_called()

    def test_the_ping_url_name_resolves(self) -> None:
        # The lock looks the URL up by name; a rename would break every request while it is on.
        self.assertEqual(reverse("ping"), "/ping/")

    @override_settings(ORIGIN_LOCK_SECRET="")
    def test_does_nothing_when_no_secret_is_configured(self) -> None:
        response = self._call()

        self.assertEqual(response.status_code, 200)
        self.get_response.assert_called_once()

    @override_settings(ORIGIN_LOCK_SECRET="sécret")
    def test_handles_non_ascii_secrets(self) -> None:
        self.assertEqual(self._call(**{HEADER_META_KEY: "sécret"}).status_code, 200)
        self.assertEqual(self._call(**{HEADER_META_KEY: "secret"}).status_code, 403)


class WorkerHeaderNameTests(SimpleTestCase):
    """The Cloudflare Worker adds the secret header; its name must match `HEADER_META_KEY`."""

    WORKER = Path(__file__).resolve().parents[2] / "deploy/terraform/prod/worker/index.js"

    def test_worker_sets_the_header_the_middleware_reads(self) -> None:
        if not self.WORKER.exists():
            self.skipTest("The Worker's source isn't in this checkout (e.g. the app image).")
        # `HTTP_X_ORIGIN_SECRET` is how Django shows the `X-Origin-Secret` header.
        header_name = HEADER_META_KEY.removeprefix("HTTP_").replace("_", "-")

        # Header names aren't case-sensitive, so compare in lower case.
        self.assertIn(f'headers.set("{header_name}"'.lower(), self.WORKER.read_text().lower())
