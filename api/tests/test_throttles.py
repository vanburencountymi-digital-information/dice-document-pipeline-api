from __future__ import annotations

from unittest.mock import PropertyMock, patch

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.test import TestCase
from parameterized import parameterized
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from accounts.models import IncompleteLimitsError, ServiceAccount
from accounts.tests.factories import ServiceAccountFactory
from api.models import ThrottleCounter
from api.tests.helpers import with_throttle_rate
from api.throttles import ClientIPThrottle
from common.throttle_scopes import ThrottleScope


class ClientIPThrottleTests(TestCase):
    def _allowed(self, ip: str) -> bool:
        request = Request(APIRequestFactory().get("/", REMOTE_ADDR=ip))
        return ClientIPThrottle().allow_request(request, None)

    @with_throttle_rate(ThrottleScope.CLIENT_IP, "2/minute")
    def test_limits_each_ip_separately(self) -> None:
        self.assertEqual([self._allowed("1.1.1.1") for _ in range(3)], [True, True, False])
        self.assertTrue(self._allowed("2.2.2.2"))

    def _allowed_as(self, account: ServiceAccount | None, times: int) -> list[bool]:
        request = Request(APIRequestFactory().get("/", REMOTE_ADDR="1.1.1.1"))
        request.user = account.user if account else AnonymousUser()
        return [ClientIPThrottle().allow_request(request, None) for _ in range(times)]

    @with_throttle_rate(ThrottleScope.CLIENT_IP, "2/minute")
    def test_unlimited_accounts_are_not_counted(self) -> None:
        account = ServiceAccountFactory(unlimited=True)

        self.assertEqual(self._allowed_as(account, 5), [True] * 5)
        self.assertEqual(ThrottleCounter.objects.count(), 0)

    @with_throttle_rate(ThrottleScope.CLIENT_IP, "2/minute")
    def test_limited_accounts_and_anonymous_callers_are_counted(self) -> None:
        account = ServiceAccountFactory()

        self.assertEqual(self._allowed_as(account, 3), [True, True, False])
        self.assertEqual(self._allowed_as(None, 1), [False])

    @with_throttle_rate(ThrottleScope.CLIENT_IP, "2/minute")
    def test_half_filled_accounts_are_not_treated_as_unlimited(self) -> None:
        account = ServiceAccountFactory()

        with patch.object(
            ServiceAccount,
            "rate_limit",
            new_callable=PropertyMock,
            side_effect=IncompleteLimitsError(account.pk),
        ):
            self.assertEqual(self._allowed_as(account, 3), [True, True, False])

    @with_throttle_rate(ThrottleScope.CLIENT_IP, "1/minute")
    def test_wait_is_the_rest_of_the_window(self) -> None:
        self._allowed("1.1.1.1")
        request = Request(APIRequestFactory().get("/", REMOTE_ADDR="1.1.1.1"))
        throttle = ClientIPThrottle()
        throttle.allow_request(request, None)

        self.assertGreater(throttle.wait(), 0)
        self.assertLessEqual(throttle.wait(), 60)


class ThrottleSettingsTests(TestCase):
    @parameterized.expand(
        [(scope,) for scope in (ThrottleScope.FAILED_AUTH, ThrottleScope.CLIENT_IP)]
    )
    def test_every_rate_throttle_scope_has_a_rate_in_settings(self, scope: ThrottleScope) -> None:
        self.assertIn(scope, settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"])

    def test_every_rate_in_settings_belongs_to_a_known_scope(self) -> None:
        known = {scope.value for scope in ThrottleScope}

        self.assertLessEqual(set(settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]), known)
