from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase

from accounts.models import ServiceAccount
from accounts.services import ServiceAccountService
from accounts.tests.factories import OrganizationFactory

User = get_user_model()


class ServiceAccountServiceTests(TestCase):
    def test_create_links_service_account_to_organization(self) -> None:
        organization = OrganizationFactory()

        service_account = ServiceAccountService().create(
            organization=organization, service_name="wordpress-prod"
        )

        self.assertEqual(service_account.organization, organization)
        self.assertEqual(service_account.name, "wordpress-prod")

    def test_create_creates_user_with_unusable_password(self) -> None:
        organization = OrganizationFactory()

        service_account = ServiceAccountService().create(
            organization=organization, service_name="wordpress-prod"
        )

        self.assertFalse(service_account.user.has_usable_password())

    def test_create_creates_auth_token(self) -> None:
        organization = OrganizationFactory()

        service_account = ServiceAccountService().create(
            organization=organization, service_name="wordpress-prod"
        )

        self.assertTrue(service_account.token)
        self.assertEqual(service_account.user.auth_token.key, service_account.token)

    def test_create_rolls_back_on_duplicate_service_name(self) -> None:
        organization = OrganizationFactory()
        ServiceAccountService().create(organization=organization, service_name="wordpress-prod")

        with self.assertRaises(IntegrityError):
            ServiceAccountService().create(organization=organization, service_name="wordpress-prod")

        self.assertEqual(User.objects.filter(username="wordpress-prod").count(), 1)
        self.assertEqual(ServiceAccount.objects.count(), 1)
