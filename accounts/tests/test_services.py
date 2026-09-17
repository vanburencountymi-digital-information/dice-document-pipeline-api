from __future__ import annotations

from django.test import TestCase

from accounts.services import ServiceAccountService
from accounts.tests.factories import OrganizationFactory


class ServiceAccountServiceTests(TestCase):
    def test_create_generates_a_webhook_secret(self) -> None:
        organization = OrganizationFactory()

        service_account, _ = ServiceAccountService().create(organization, "test-service")

        self.assertTrue(service_account.webhook_secret)
        self.assertEqual(len(service_account.webhook_secret), 64)

    def test_create_generates_distinct_secrets_per_account(self) -> None:
        organization = OrganizationFactory()
        service = ServiceAccountService()

        first, _ = service.create(organization, "service-a")
        second, _ = service.create(organization, "service-b")

        self.assertNotEqual(first.webhook_secret, second.webhook_secret)
