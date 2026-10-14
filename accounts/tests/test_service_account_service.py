from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase
from knox.models import AuthToken

from accounts.models import ServiceAccount
from accounts.services import ServiceAccountService, TokenNameTakenError
from accounts.tests.factories import OrganizationFactory

User = get_user_model()


class ServiceAccountServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.organization = OrganizationFactory()

    def test_create_links_service_account_to_organization(self) -> None:
        service_account, _ = ServiceAccountService().create(
            organization=self.organization, service_name="wordpress-prod", token_name="first"
        )

        self.assertEqual(service_account.organization, self.organization)
        self.assertEqual(service_account.name, "wordpress-prod")

    def test_create_creates_user_with_unusable_password(self) -> None:
        service_account, _ = ServiceAccountService().create(
            organization=self.organization, service_name="wordpress-prod", token_name="first"
        )

        self.assertFalse(service_account.user.has_usable_password())

    def test_create_generates_a_username_instead_of_using_the_name(self) -> None:
        first, _ = ServiceAccountService().create(self.organization, "wordpress-prod", "first")
        second, _ = ServiceAccountService().create(OrganizationFactory(), "wordpress-prod", "first")

        self.assertNotEqual(first.user.username, "wordpress-prod")
        self.assertNotEqual(first.user.username, second.user.username)

    def test_create_creates_auth_token(self) -> None:
        service_account, token = ServiceAccountService().create(
            organization=self.organization, service_name="wordpress-prod", token_name="first"
        )

        self.assertTrue(token)
        self.assertEqual(service_account.user.auth_token_set.count(), 1)

    def test_create_rolls_back_on_duplicate_service_name(self) -> None:
        ServiceAccountService().create(
            organization=self.organization, service_name="wordpress-prod", token_name="first"
        )

        with self.assertRaises(IntegrityError):
            ServiceAccountService().create(
                organization=self.organization, service_name="wordpress-prod", token_name="first"
            )

        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(ServiceAccount.objects.count(), 1)

    def test_create_names_the_first_token(self) -> None:
        service_account, _ = ServiceAccountService().create(
            self.organization, "wordpress-prod", token_name="first"
        )

        self.assertEqual(list(service_account.tokens.values_list("name", flat=True)), ["first"])

    def test_issue_token_adds_a_named_token_without_disturbing_the_first(self) -> None:
        service_account, first_token = ServiceAccountService().create(
            organization=self.organization, service_name="wordpress-prod", token_name="first"
        )

        second_token = ServiceAccountService().issue_token(service_account, "second")

        self.assertNotEqual(first_token, second_token)
        self.assertEqual(service_account.user.auth_token_set.count(), 2)
        self.assertEqual(service_account.tokens.filter(name="second").count(), 1)

    def test_issue_token_rolls_back_on_duplicate_token_name(self) -> None:
        service_account, _ = ServiceAccountService().create(
            self.organization, "wordpress-prod", token_name="dup"
        )

        with self.assertRaises(TokenNameTakenError):
            ServiceAccountService().issue_token(service_account, "dup")

        self.assertEqual(service_account.user.auth_token_set.count(), 1)

    def test_revoke_token_removes_only_that_token(self) -> None:
        service = ServiceAccountService()
        service_account, _ = service.create(self.organization, "wordpress-prod", token_name="a")
        service.issue_token(service_account, "b")

        service.revoke_token(service_account.tokens.get(name="a"))

        self.assertEqual(list(service_account.tokens.values_list("name", flat=True)), ["b"])
        self.assertEqual(service_account.user.auth_token_set.count(), 1)

    def test_revoke_tokens_is_all_or_nothing(self) -> None:
        service = ServiceAccountService()
        service_account, _ = service.create(self.organization, "wordpress-prod", token_name="a")
        service.issue_token(service_account, "b")
        first = service_account.tokens.select_related("auth_token").get(name="a")

        # The second entry fails after the first has already been revoked
        with self.assertRaises(AttributeError):
            service.revoke_tokens([first, object()])

        self.assertEqual(service_account.tokens.count(), 2)

    def test_delete_removes_the_login_and_its_tokens(self) -> None:
        service = ServiceAccountService()
        service_account, _ = service.create(self.organization, "wordpress-prod", token_name="a")
        user_pk = service_account.user.pk

        service.delete(service_account)

        self.assertFalse(User.objects.filter(pk=user_pk).exists())
        self.assertFalse(ServiceAccount.objects.filter(pk=service_account.pk).exists())
        self.assertFalse(AuthToken.objects.filter(user_id=user_pk).exists())

    def test_regenerate_webhook_secret_saves_a_new_secret(self) -> None:
        account, _ = ServiceAccountService().create(self.organization, "wordpress-prod", "first")
        old_secret = account.webhook_secret

        new_secret = ServiceAccountService().regenerate_webhook_secret(account)

        account.refresh_from_db()
        self.assertNotEqual(old_secret, new_secret)
        self.assertEqual(account.webhook_secret, new_secret)
