from __future__ import annotations

from django.test import TestCase
from django.urls import reverse
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from accounts.tests.factories import ServiceAccountFactory
from api.views import ServiceAccountRequiredMixin, StatusView


class StatusViewTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.url = reverse("status")

    def setUp(self) -> None:
        self.factory = APIRequestFactory()
        self.view = StatusView.as_view()

    def test_valid_token_returns_200(self) -> None:
        service_account = ServiceAccountFactory()

        request = self.factory.get(self.url, HTTP_AUTHORIZATION=f"Token {service_account.token}")
        response = self.view(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"status": "ok"})

    def test_missing_token_returns_401(self) -> None:
        request = self.factory.get(self.url)
        response = self.view(request)

        self.assertEqual(response.status_code, 401)

    def test_invalid_token_returns_401(self) -> None:
        request = self.factory.get(self.url, HTTP_AUTHORIZATION="Token bogus")
        response = self.view(request)

        self.assertEqual(response.status_code, 401)


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
