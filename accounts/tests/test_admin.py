from __future__ import annotations

from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, TestCase

from accounts.admin import ServiceAccountAdmin
from accounts.models import ServiceAccount
from accounts.tests.factories import ServiceAccountFactory


class ServiceAccountAdminTests(TestCase):
    def setUp(self) -> None:
        self.admin = ServiceAccountAdmin(ServiceAccount, AdminSite())
        self.request = RequestFactory().get("/admin/")
        SessionMiddleware(lambda request: None).process_request(self.request)
        self.request.session.save()
        self.request._messages = FallbackStorage(self.request)  # type: ignore[attr-defined]

    def test_default_add_permission_is_always_denied(self) -> None:
        self.assertFalse(self.admin.has_add_permission(self.request))

    @patch("accounts.admin.ServiceAccountService", autospec=True, spec_set=True)
    def test_issue_token_calls_service_for_each_selected_account(self, mock_service_cls) -> None:
        mock_service_cls.return_value.issue_token.return_value = "new-token"
        service_account = ServiceAccountFactory()
        queryset = ServiceAccount.objects.filter(pk=service_account.pk)

        self.admin.issue_token(self.request, queryset)

        mock_service_cls.return_value.issue_token.assert_called_once_with(service_account)
