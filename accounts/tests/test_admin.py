from __future__ import annotations

from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.contrib.messages import get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, TestCase
from parameterized import parameterized

from accounts.admin import ServiceAccountAdmin
from accounts.models import ServiceAccount
from accounts.tests.factories import OrganizationFactory, ServiceAccountFactory, UserFactory


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

    def test_add_permission_is_allowed(self) -> None:
        self.request.user = UserFactory(is_staff=True, is_superuser=True)
        self.assertTrue(self.admin.has_add_permission(self.request))

    def test_add_form_only_asks_for_organization_and_name(self) -> None:
        form_class = self.admin.get_form(self.request, None)
        self.assertEqual(list(form_class.base_fields), ["organization", "name"])

    def test_edit_form_shows_the_other_fields(self) -> None:
        fields = self.admin.get_fields(self.request, ServiceAccountFactory())
        self.assertIn("webhook_secret", fields)

    def test_add_page_lists_only_organization_and_name(self) -> None:
        self.assertEqual(list(self.admin.get_fields(self.request, None)), ["organization", "name"])

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

        self.admin.save_model(self.request, obj, form=None, change=False)

        mock_service_cls.return_value.create.assert_called_once_with(organization, "wordpress-prod")
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
            self.admin.save_model(self.request, service_account, form=None, change=False)

        mock_service_cls.return_value.create.assert_not_called()

    @patch("accounts.admin.ServiceAccountService", autospec=True, spec_set=True)
    def test_issue_token_calls_service_for_each_selected_account(self, mock_service_cls) -> None:
        mock_service_cls.return_value.issue_token.return_value = "new-token"
        service_account = ServiceAccountFactory()
        queryset = ServiceAccount.objects.filter(pk=service_account.pk)

        self.admin.issue_token(self.request, queryset)

        mock_service_cls.return_value.issue_token.assert_called_once_with(service_account)


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
            ("same_name_same_organization", "organization", "wordpress-prod", False),
            ("same_name_other_organization", "other_organization", "wordpress-prod", True),
            ("new_name_same_organization", "organization", "wordpress-test", True),
            ("blank_name", "organization", "", False),
        ]
    )
    def test_validation(self, _label: str, org_attr: str, name: str, valid: bool) -> None:
        form = self.form_class({"organization": getattr(self, org_attr).pk, "name": name})

        self.assertEqual(form.is_valid(), valid, form.errors)
