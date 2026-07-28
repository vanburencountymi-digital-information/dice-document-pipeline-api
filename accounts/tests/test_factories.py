from __future__ import annotations

from django.test import TestCase

from accounts.tests.factories import ServiceAccountFactory


class ServiceAccountFactoryTests(TestCase):
    def test_creates_a_working_token(self) -> None:
        service_account = ServiceAccountFactory()

        self.assertTrue(service_account.token)
        self.assertEqual(service_account.user.auth_token.key, service_account.token)
