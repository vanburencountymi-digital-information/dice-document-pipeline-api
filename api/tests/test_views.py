from __future__ import annotations

from unittest.mock import PropertyMock, patch

from django.test import TestCase
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from accounts.models import IncompleteLimitsError, ServiceAccount
from accounts.tests.factories import ServiceAccountFactory
from api.tests.helpers import with_throttle_rate
from api.views import EnforceAccountLimitsMixin, PingView, ServiceAccountRequiredMixin
from common.throttle_scopes import ThrottleScope


class _ServiceAccountRequiredMixinTestView(ServiceAccountRequiredMixin, APIView):
    """Throwaway view; exists only so the mixin's auth/property contract can be tested
    independently of any real view that happens to use it."""

    def get(self, request: Request) -> Response:
        return Response({"service_account_id": self.service_account.id})


class ServiceAccountRequiredMixinTests(TestCase):
    def setUp(self) -> None:
        self.factory = APIRequestFactory()
        self.view = _ServiceAccountRequiredMixinTestView.as_view()

    def test_missing_token_returns_401(self) -> None:
        request = self.factory.get("/")
        response = self.view(request)

        self.assertEqual(response.status_code, 401)

    def test_invalid_token_returns_401(self) -> None:
        request = self.factory.get("/", HTTP_AUTHORIZATION="Token bogus")
        response = self.view(request)

        self.assertEqual(response.status_code, 401)

    def test_service_account_property_resolves_authenticated_users_account(self) -> None:
        service_account = ServiceAccountFactory()
        request = self.factory.get("/", HTTP_AUTHORIZATION=f"Token {service_account.token}")

        response = self.view(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["service_account_id"], service_account.id)


class _EnforceAccountLimitsTestView(EnforceAccountLimitsMixin):
    """Throwaway view; exists only so the mixin's limits/headers contract can be tested
    independently of any real view."""

    def get(self, request: Request) -> Response:
        return Response({"ok": True})


class EnforceAccountLimitsMixinTests(TestCase):
    def setUp(self) -> None:
        self.factory = APIRequestFactory()
        self.view = _EnforceAccountLimitsTestView.as_view()

    def _get(self, service_account: ServiceAccount):
        request = self.factory.get("/", HTTP_AUTHORIZATION=f"Token {service_account.token}")
        return self.view(request)

    def test_every_response_carries_rate_limit_headers(self) -> None:
        response = self._get(ServiceAccountFactory(rate_limit_count=7, rate_limit_period="hour"))

        self.assertEqual(response["X-RateLimit-Limit"], "7")
        self.assertEqual(response["X-RateLimit-Remaining"], "7")
        self.assertEqual(response["X-RateLimit-Reset"], "0")

    def test_unlimited_account_gets_no_headers(self) -> None:
        response = self._get(ServiceAccountFactory(unlimited=True))

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("X-RateLimit-Limit", response)

    def test_incomplete_limits_are_denied(self) -> None:
        service_account = ServiceAccountFactory()

        with patch.object(
            ServiceAccount,
            "rate_limit",
            new_callable=PropertyMock,
            side_effect=IncompleteLimitsError(service_account.pk),
        ):
            response = self._get(service_account)

        self.assertEqual(response.status_code, 403)
        self.assertNotIn("X-RateLimit-Limit", response)

    def test_missing_token_returns_401_without_headers(self) -> None:
        response = self.view(self.factory.get("/"))

        self.assertEqual(response.status_code, 401)
        self.assertNotIn("X-RateLimit-Limit", response)


class PingViewTests(TestCase):
    def setUp(self) -> None:
        self.view = PingView.as_view()

    def test_answers_pong_without_a_token(self) -> None:
        response = self.view(APIRequestFactory().get("/ping/"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"pong!")

    def test_ignores_a_bad_token(self) -> None:
        request = APIRequestFactory().get("/ping/", HTTP_AUTHORIZATION="Token bogus")

        self.assertEqual(self.view(request).status_code, 200)

    @with_throttle_rate(ThrottleScope.CLIENT_IP, "1/minute")
    def test_is_throttled_per_ip(self) -> None:
        request = APIRequestFactory().get("/ping/", REMOTE_ADDR="1.1.1.1")

        self.assertEqual(self.view(request).status_code, 200)
        self.assertEqual(self.view(request).status_code, 429)
