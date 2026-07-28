from __future__ import annotations

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIRequestFactory

from accounts.tests.factories import ServiceAccountFactory
from api.views import StatusView


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
