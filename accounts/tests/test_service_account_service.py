from __future__ import annotations

from django.contrib.auth import authenticate, get_user_model
from django.db import IntegrityError
from django.test import TestCase, override_settings
from knox.models import AuthToken
from parameterized import parameterized

from accounts.models import RateLimitPeriod, ServiceAccount
from accounts.services import (
    ServiceAccountNameTakenError,
    ServiceAccountService,
    TokenNameTakenError,
)
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

    @override_settings(DEFAULT_MAX_IN_FLIGHT=25, DEFAULT_RATE_LIMIT_COUNT=200)
    def test_create_uses_the_default_limits_from_settings(self) -> None:
        service_account, _ = ServiceAccountService().create(self.organization, "wordpress", "first")

        self.assertFalse(service_account.unlimited)
        self.assertEqual(service_account.in_flight_cap, 25)
        self.assertEqual(service_account.rate_limit, f"200/{RateLimitPeriod.DAY}")

    def test_create_saves_the_given_limits(self) -> None:
        service_account, _ = ServiceAccountService().create(
            self.organization,
            "wordpress",
            "first",
            max_in_flight=50,
            rate_limit_count=1000,
            rate_limit_period=RateLimitPeriod.HOUR,
        )

        service_account.refresh_from_db()
        self.assertEqual(service_account.in_flight_cap, 50)
        self.assertEqual(service_account.rate_limit, "1000/hour")

    def test_create_unlimited_allows_blank_values(self) -> None:
        service_account, _ = ServiceAccountService().create(
            self.organization,
            "wordpress",
            "first",
            unlimited=True,
            max_in_flight=None,
            rate_limit_count=None,
            rate_limit_period=None,
        )

        service_account.refresh_from_db()
        self.assertIsNone(service_account.in_flight_cap)
        self.assertIsNone(service_account.rate_limit)

    @parameterized.expand(
        [("typo", {"max_inflight": 5}), ("other_model_field", {"webhook_secret": "chosen"})]
    )
    def test_create_refuses_keywords_that_are_not_limit_fields(
        self, _label: str, limits: dict
    ) -> None:
        with self.assertRaises(TypeError):
            ServiceAccountService().create(self.organization, "wordpress", "first", **limits)

        self.assertFalse(ServiceAccount.objects.filter(name="wordpress").exists())
        self.assertFalse(User.objects.exists())

    def test_create_refuses_blank_limits_without_unlimited(self) -> None:
        with self.assertRaises(IntegrityError):
            ServiceAccountService().create(
                self.organization, "wordpress", "first", max_in_flight=None
            )

        self.assertFalse(ServiceAccount.objects.filter(name="wordpress").exists())

    def test_create_creates_user_with_unusable_password(self) -> None:
        service_account, _ = ServiceAccountService().create(
            organization=self.organization, service_name="wordpress-prod", token_name="first"
        )

        self.assertFalse(service_account.user.has_usable_password())

    @parameterized.expand(
        [("empty", ""), ("unusable_marker", "!"), ("guess", "password"), ("username", "service")]
    )
    def test_create_user_cannot_log_in_with_a_password(self, _label: str, password: str) -> None:
        service_account, _ = ServiceAccountService().create(
            organization=self.organization, service_name="wordpress-prod", token_name="first"
        )

        user = authenticate(username=service_account.user.username, password=password)

        self.assertIsNone(user)

    def test_create_user_is_not_staff_or_superuser(self) -> None:
        service_account, _ = ServiceAccountService().create(
            organization=self.organization, service_name="wordpress-prod", token_name="first"
        )

        self.assertFalse(service_account.user.is_staff)
        self.assertFalse(service_account.user.is_superuser)

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

        with self.assertRaises(ServiceAccountNameTakenError):
            ServiceAccountService().create(
                organization=self.organization, service_name="wordpress-prod", token_name="first"
            )

        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(ServiceAccount.objects.count(), 1)

    def test_create_with_a_taken_name_in_another_organization_is_fine(self) -> None:
        ServiceAccountService().create(self.organization, "wordpress-prod", "first")

        service_account, _ = ServiceAccountService().create(
            OrganizationFactory(), "wordpress-prod", "first"
        )

        self.assertEqual(ServiceAccount.objects.count(), 2)
        self.assertEqual(service_account.name, "wordpress-prod")

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
