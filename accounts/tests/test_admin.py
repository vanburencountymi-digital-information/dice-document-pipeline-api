from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from django.contrib import admin
from django.contrib.admin.sites import AdminSite
from django.contrib.messages import get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, TestCase
from django.urls import reverse
from knox.models import AuthToken
from parameterized import parameterized

from accounts.admin import LimitDisplay, ServiceAccountAdmin, ServiceAccountTokenAdmin
from accounts.models import (
    LIMIT_FIELDS,
    Organization,
    RateLimitPeriod,
    ServiceAccount,
    ServiceAccountToken,
)
from accounts.services import ServiceAccountNameTakenError, TokenNameTakenError
from accounts.tests.factories import (
    OrganizationFactory,
    ServiceAccountFactory,
    ServiceAccountTokenFactory,
    UserFactory,
)

ADD_FIELDS = ["organization", "name", *LIMIT_FIELDS, "token_name"]


def _request():
    request = RequestFactory().get("/admin/")
    SessionMiddleware(lambda request: None).process_request(request)
    request.session.save()
    request._messages = FallbackStorage(request)
    return request


class ServiceAccountAdminTests(TestCase):
    def setUp(self) -> None:
        self.admin = ServiceAccountAdmin(ServiceAccount, AdminSite())
        self.request = _request()
        self.request.user = UserFactory(is_staff=True, is_superuser=True)
        self.add_form = SimpleNamespace(cleaned_data={"token_name": "first"})

    def test_add_permission_is_allowed(self) -> None:
        self.request.user = UserFactory(is_staff=True, is_superuser=True)
        self.assertTrue(self.admin.has_add_permission(self.request))

    def test_add_form_only_asks_for_what_a_person_chooses(self) -> None:
        form_class = self.admin.get_form(self.request, None)
        self.assertEqual(list(form_class.base_fields), ADD_FIELDS)

    def test_edit_form_shows_the_other_fields(self) -> None:
        fields = self.admin.get_fields(self.request, ServiceAccountFactory())
        self.assertIn("webhook_secret", fields)
        self.assertIn("max_in_flight", fields)
        self.assertIn("rate_limit_count", fields)
        self.assertIn("rate_limit_period", fields)

    def test_add_page_lists_only_what_a_person_chooses(self) -> None:
        self.assertEqual(list(self.admin.get_fields(self.request, None)), ADD_FIELDS)

    @parameterized.expand(
        [
            ("defaults", {}, "10", "100/day"),
            (
                "custom",
                {
                    "max_in_flight": 50,
                    "rate_limit_count": 5,
                    "rate_limit_period": RateLimitPeriod.HOUR,
                },
                "50",
                "5/hour",
            ),
            ("unlimited", {"unlimited": True}, LimitDisplay.UNLIMITED, LimitDisplay.UNLIMITED),
            (
                "incomplete_row",
                {"max_in_flight": None, "rate_limit_count": None},
                LimitDisplay.INCOMPLETE,
                LimitDisplay.INCOMPLETE,
            ),
        ]
    )
    def test_list_columns_show_the_limits(
        self, _label: str, fields: dict, in_flight_cap: str, rate_limit: str
    ) -> None:
        # Built unsaved: an "incomplete" row is refused by the database, but the list page must
        # still cope with one rather than crash.
        # Pinned so the "defaults" case doesn't depend on a local .env
        with self.settings(DEFAULT_MAX_IN_FLIGHT=10, DEFAULT_RATE_LIMIT_COUNT=100):
            account = ServiceAccount(**fields)

        self.assertEqual(self.admin.in_flight_cap(account), in_flight_cap)
        self.assertEqual(self.admin.rate_limit(account), rate_limit)

    def test_user_and_webhook_secret_are_read_only_when_editing(self) -> None:
        service_account = ServiceAccountFactory()
        self.assertEqual(
            self.admin.get_readonly_fields(self.request, service_account),
            ("user", "webhook_secret"),
        )
        self.assertEqual(self.admin.get_readonly_fields(self.request, None), ())

    @patch("accounts.admin.ServiceAccountService", autospec=True, spec_set=True)
    def test_save_model_on_add_goes_through_the_service(self, mock_service_cls) -> None:
        organization = OrganizationFactory()
        created = ServiceAccountFactory(organization=organization, name="wordpress-prod")
        mock_service_cls.return_value.create.return_value = (created, "new-token")
        obj = ServiceAccount(
            organization=organization,
            name="wordpress-prod",
            unlimited=True,
            max_in_flight=None,
            rate_limit_count=5,
            rate_limit_period=RateLimitPeriod.MINUTE,
        )

        self.admin.save_model(self.request, obj, form=self.add_form, change=False)

        mock_service_cls.return_value.create.assert_called_once_with(
            organization,
            "wordpress-prod",
            "first",
            unlimited=True,
            max_in_flight=None,
            rate_limit_count=5,
            rate_limit_period=RateLimitPeriod.MINUTE,
        )
        self.assertEqual(obj.pk, created.pk)
        self.assertIn("new-token", [m.message for m in get_messages(self.request)][0])

    @patch("accounts.admin.ServiceAccountService", autospec=True, spec_set=True)
    def test_save_model_on_change_is_a_plain_save(self, mock_service_cls) -> None:
        service_account = ServiceAccountFactory(name="before")
        service_account.name = "after"

        self.admin.save_model(
            self.request, service_account, form=SimpleNamespace(changed_data=["name"]), change=True
        )

        service_account.refresh_from_db()
        self.assertEqual(service_account.name, "after")
        mock_service_cls.return_value.create.assert_not_called()
        self.assertEqual(list(get_messages(self.request)), [])

    def test_save_model_on_change_keeps_a_secret_regenerated_meanwhile(self) -> None:
        stale = ServiceAccount.objects.get(pk=ServiceAccountFactory(max_in_flight=5).pk)
        ServiceAccount.objects.filter(pk=stale.pk).update(webhook_secret="regenerated")
        stale.max_in_flight = 7

        self.admin.save_model(
            self.request, stale, form=SimpleNamespace(changed_data=["max_in_flight"]), change=True
        )

        saved = ServiceAccount.objects.get(pk=stale.pk)
        self.assertEqual(saved.max_in_flight, 7)
        self.assertEqual(saved.webhook_secret, "regenerated")

    @patch("accounts.admin.ServiceAccountService", autospec=True, spec_set=True)
    def test_save_model_refuses_to_create_credentials_for_an_existing_account(
        self, mock_service_cls
    ) -> None:
        service_account = ServiceAccountFactory()

        with self.assertRaises(ValueError):
            self.admin.save_model(self.request, service_account, form=self.add_form, change=False)

        mock_service_cls.return_value.create.assert_not_called()

    @patch("accounts.admin.ServiceAccountService", autospec=True, spec_set=True)
    def test_delete_model_goes_through_the_service(self, mock_service_cls) -> None:
        service_account = ServiceAccountFactory()

        self.admin.delete_model(self.request, service_account)

        mock_service_cls.return_value.delete.assert_called_once_with(service_account)

    def test_list_has_no_bulk_actions(self) -> None:
        self.assertEqual(self.admin.get_actions(self.request), {})

    def test_knox_auth_token_admin_is_not_registered(self) -> None:
        self.assertNotIn(AuthToken, admin.site._registry)

    def test_token_message_escapes_html_and_is_copyable(self) -> None:
        message = ServiceAccountAdmin._token_message("<b>x</b>", "tok")

        self.assertIn("&lt;b&gt;", message)
        self.assertIn("<code", message)


class ServiceAccountAdminViewTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.superuser = UserFactory(is_staff=True, is_superuser=True)
        cls.service_account = ServiceAccountFactory()
        cls.issue_url = reverse(
            "admin:accounts_serviceaccount_issue_token", args=[cls.service_account.pk]
        )
        cls.regenerate_url = reverse(
            "admin:accounts_serviceaccount_regenerate_webhook_secret",
            args=[cls.service_account.pk],
        )
        cls.change_url = reverse(
            "admin:accounts_serviceaccount_change", args=[cls.service_account.pk]
        )

    def setUp(self) -> None:
        self.client.force_login(self.superuser)

    def test_change_page_has_buttons_for_both_actions(self) -> None:
        response = self.client.get(self.change_url)

        self.assertContains(response, self.issue_url)
        self.assertContains(response, self.regenerate_url)

    @patch("accounts.admin.ServiceAccountService", autospec=True, spec_set=True)
    def test_add_with_a_name_taken_at_the_same_moment_shows_a_message_not_an_error(
        self, mock_service_cls
    ) -> None:
        mock_service_cls.return_value.create.side_effect = ServiceAccountNameTakenError("x")
        add_url = reverse("admin:accounts_serviceaccount_add")
        data = {
            "organization": OrganizationFactory().pk,
            "name": "wordpress-prod",
            "token_name": "first",
            "max_in_flight": 10,
            "rate_limit_count": 100,
            "rate_limit_period": RateLimitPeriod.DAY,
            # The page's (read-only) tokens inline still posts its management form
            "tokens-TOTAL_FORMS": 0,
            "tokens-INITIAL_FORMS": 0,
            "tokens-MIN_NUM_FORMS": 0,
            "tokens-MAX_NUM_FORMS": 0,
        }

        response = self.client.post(add_url, data, follow=True)

        self.assertRedirects(response, add_url)
        self.assertContains(response, "already has a service account with that name")

    def test_issue_token_page_shows_the_name_form(self) -> None:
        response = self.client.get(self.issue_url)

        self.assertTemplateUsed(response, "admin/accounts/issue_token.html")

    @patch("accounts.admin.ServiceAccountService", autospec=True, spec_set=True)
    def test_issue_token_with_a_name_calls_the_service_and_shows_the_token(
        self, mock_service_cls
    ) -> None:
        mock_service_cls.return_value.issue_token.return_value = "new-token"

        response = self.client.post(self.issue_url, {"name": "wordpress-2026"}, follow=True)

        mock_service_cls.return_value.issue_token.assert_called_once_with(
            self.service_account, "wordpress-2026"
        )
        self.assertRedirects(response, self.change_url)
        self.assertContains(response, "new-token")

    @patch("accounts.admin.ServiceAccountService", autospec=True, spec_set=True)
    def test_issue_token_with_a_taken_name_asks_again(self, mock_service_cls) -> None:
        mock_service_cls.return_value.issue_token.side_effect = TokenNameTakenError("taken")

        response = self.client.post(self.issue_url, {"name": "taken"})

        self.assertContains(response, "already has a token with that name")

    @patch("accounts.admin.ServiceAccountService", autospec=True, spec_set=True)
    def test_regenerate_page_does_nothing_until_confirmed(self, mock_service_cls) -> None:
        response = self.client.get(self.regenerate_url)

        self.assertTemplateUsed(response, "admin/accounts/regenerate_webhook_secret.html")
        mock_service_cls.return_value.regenerate_webhook_secret.assert_not_called()

    @patch("accounts.admin.ServiceAccountService", autospec=True, spec_set=True)
    def test_regenerate_confirmed_calls_the_service(self, mock_service_cls) -> None:
        response = self.client.post(self.regenerate_url)

        mock_service_cls.return_value.regenerate_webhook_secret.assert_called_once_with(
            self.service_account
        )
        self.assertRedirects(response, self.change_url)

    @parameterized.expand([("issue", "issue_url"), ("regenerate", "regenerate_url")])
    def test_unknown_account_is_a_404(self, _label: str, url_attr: str) -> None:
        url = getattr(self, url_attr).replace(str(self.service_account.pk), "999999")

        self.assertEqual(self.client.get(url).status_code, 404)

    @parameterized.expand([("issue", "issue_url"), ("regenerate", "regenerate_url")])
    def test_non_staff_cannot_use_the_pages(self, _label: str, url_attr: str) -> None:
        self.client.force_login(UserFactory(is_staff=False))

        response = self.client.get(getattr(self, url_attr))

        self.assertEqual(response.status_code, 302)  # sent to the admin login


class ServiceAccountAddFormTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.organization = OrganizationFactory()
        cls.other_organization = OrganizationFactory()
        ServiceAccountFactory(organization=cls.organization, name="wordpress-prod")

    def setUp(self) -> None:
        self.form_class = ServiceAccountAdmin(ServiceAccount, AdminSite()).get_form(
            _request(), None
        )

    @parameterized.expand(
        [
            ("same_name_same_organization", "organization", "wordpress-prod", "first", False),
            ("same_name_other_organization", "other_organization", "wordpress-prod", "first", True),
            ("new_name_same_organization", "organization", "wordpress-test", "first", True),
            ("blank_name", "organization", "", "first", False),
            ("blank_token_name", "organization", "wordpress-test", "", False),
        ]
    )
    def test_validation(
        self, _label: str, org_attr: str, name: str, token_name: str, valid: bool
    ) -> None:
        form = self.form_class(
            {"organization": getattr(self, org_attr).pk, "name": name, "token_name": token_name}
        )

        self.assertEqual(form.is_valid(), valid, form.errors)

    @parameterized.expand(
        [
            ("all_set", False, "20", "5", RateLimitPeriod.MINUTE, True),
            ("all_set_and_unlimited", True, "20", "5", RateLimitPeriod.MINUTE, True),
            ("all_blank_and_unlimited", True, "", "", "", True),
            ("all_blank_not_unlimited", False, "", "", "", False),
            ("max_in_flight_blank", False, "", "5", RateLimitPeriod.HOUR, False),
            ("count_blank", False, "10", "", RateLimitPeriod.DAY, False),
            ("period_blank", False, "10", "100", "", False),
            ("zero_in_flight_blocks_all", False, "0", "100", RateLimitPeriod.DAY, True),
            ("negative_in_flight", False, "-1", "100", RateLimitPeriod.DAY, False),
            ("unknown_period", False, "10", "100", "week", False),
            ("zero_count_blocks_all", False, "10", "0", RateLimitPeriod.DAY, True),
            ("negative_count", False, "10", "-1", RateLimitPeriod.DAY, False),
        ]
    )
    def test_limits_validation(
        self,
        _label: str,
        unlimited: bool,
        max_in_flight: str,
        count: str,
        period: str,
        valid: bool,
    ) -> None:
        form = self.form_class(
            {
                "organization": self.organization.pk,
                "name": "wordpress-test",
                "token_name": "first",
                "unlimited": unlimited,
                "max_in_flight": max_in_flight,
                "rate_limit_count": count,
                "rate_limit_period": period,
            }
        )

        self.assertEqual(form.is_valid(), valid, form.errors)


class OrganizationAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.staff = UserFactory(is_staff=True, is_superuser=True)
        cls.organization = OrganizationFactory()

    def setUp(self) -> None:
        self.client.force_login(self.staff)

    def test_add_page_only_asks_for_a_name(self) -> None:
        response = self.client.get(reverse("admin:accounts_organization_add"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["adminform"].form.fields), ["name"])

    def test_adding_an_organization_saves_it(self) -> None:
        response = self.client.post(reverse("admin:accounts_organization_add"), {"name": "Acme"})

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Organization.objects.filter(name="Acme").exists())

    def test_a_taken_name_shows_an_error_not_a_crash(self) -> None:
        response = self.client.post(
            reverse("admin:accounts_organization_add"), {"name": self.organization.name}
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["adminform"].form.errors["name"])


class ServiceAccountTokenAdminTests(TestCase):
    def setUp(self) -> None:
        self.admin = ServiceAccountTokenAdmin(ServiceAccountToken, AdminSite())
        self.request = _request()

    def test_tokens_cannot_be_added_or_changed_by_hand(self) -> None:
        self.assertFalse(self.admin.has_add_permission(self.request))
        self.assertFalse(self.admin.has_change_permission(self.request))

    @patch("accounts.admin.ServiceAccountService", autospec=True, spec_set=True)
    def test_delete_model_revokes_through_the_service(self, mock_service_cls) -> None:
        token = ServiceAccountTokenFactory()

        self.admin.delete_model(self.request, token)

        mock_service_cls.return_value.revoke_token.assert_called_once_with(token)

    @patch("accounts.admin.ServiceAccountService", autospec=True, spec_set=True)
    def test_delete_queryset_revokes_all_in_one_service_call(self, mock_service_cls) -> None:
        ServiceAccountTokenFactory.create_batch(2)

        self.admin.delete_queryset(self.request, ServiceAccountToken.objects.all())

        mock_service_cls.return_value.revoke_tokens.assert_called_once()
