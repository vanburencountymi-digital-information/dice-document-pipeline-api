from __future__ import annotations

from django.test import TestCase
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory

from accounts.tests.factories import ServiceAccountFactory
from api.tests.helpers import with_throttle_rate
from api.views import ServiceAccountRequiredMixin
from common.throttle_scopes import ThrottleScope


class _AuthTestView(ServiceAccountRequiredMixin):
    def get(self, request: Request) -> Response:
        return Response({"ok": True})


@with_throttle_rate(ThrottleScope.FAILED_AUTH, "2/hour")
class LockoutTokenAuthenticationTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.service_account = ServiceAccountFactory()

    def setUp(self) -> None:
        self.factory = APIRequestFactory()
        self.view = _AuthTestView.as_view()

    def _get(self, token: str | None, ip: str = "1.1.1.1"):
        extra = {"REMOTE_ADDR": ip}
        if token is not None:
            extra["HTTP_AUTHORIZATION"] = f"Token {token}"
        return self.view(self.factory.get("/", **extra))

    def test_wrong_tokens_get_401_then_429_with_retry_after(self) -> None:
        self.assertEqual(self._get("bogus").status_code, 401)
        self.assertEqual(self._get("bogus").status_code, 401)

        response = self._get("bogus")

        self.assertEqual(response.status_code, 429)
        self.assertIn("Retry-After", response)

    def test_a_locked_out_ip_cannot_log_in_even_with_a_valid_token(self) -> None:
        self._get("bogus")
        self._get("bogus")

        self.assertEqual(self._get(self.service_account.token).status_code, 429)

    def test_other_ips_are_unaffected(self) -> None:
        self._get("bogus")
        self._get("bogus")

        self.assertEqual(self._get(self.service_account.token, ip="2.2.2.2").status_code, 200)

    def test_missing_token_is_not_counted(self) -> None:
        for _ in range(5):
            self.assertEqual(self._get(None).status_code, 401)

    def test_valid_tokens_are_not_counted(self) -> None:
        for _ in range(5):
            self.assertEqual(self._get(self.service_account.token).status_code, 200)
