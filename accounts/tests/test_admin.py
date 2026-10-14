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

from accounts.admin import ServiceAccountAdmin, ServiceAccountTokenAdmin
from accounts.models import ServiceAccount, ServiceAccountToken
from accounts.services import TokenNameTakenError
from accounts.tests.factories import (
    OrganizationFactory,
    ServiceAccountFactory,
    ServiceAccountTokenFactory,
    UserFactory,
)


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

    def test_add_form_only_asks_for_organization_and_name(self) -> None:
        form_class = self.admin.get_form(self.request, None)
        self.assertEqual(list(form_class.base_fields), ["organization", "name", "token_name"])

    def test_edit_form_shows_the_other_fields(self) -> None:
        fields = self.admin.get_fields(self.request, ServiceAccountFactory())
        self.assertIn("webhook_secret", fields)

    def test_add_page_lists_only_organization_and_name(self) -> None:
        self.assertEqual(
            list(self.admin.get_fields(self.request, None)), ["organization", "name", "token_name"]
        )

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
        obj = ServiceAccount(organization=organization, name="wordpress-prod")

        self.admin.save_model(self.request, obj, form=self.add_form, change=False)

        mock_service_cls.return_value.create.assert_called_once_with(
            organization, "wordpress-prod", "first"
        )
        self.assertEqual(obj.pk, created.pk)
        self.assertIn("new-token", [m.message for m in get_messages(self.request)][0])

    @patch("accounts.admin.ServiceAccountService", autospec=True, spec_set=True)
    def test_save_model_on_change_is_a_plain_save(self, mock_service_cls) -> None:
        service_account = ServiceAccountFactory(name="before")
        service_account.name = "after"

        self.admin.save_model(self.request, service_account, form=None, change=True)

        service_account.refresh_from_db()
        self.assertEqual(service_account.name, "after")
        mock_service_cls.return_value.create.assert_not_called()
        self.assertEqual(list(get_messages(self.request)), [])

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
