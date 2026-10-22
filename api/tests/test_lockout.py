from __future__ import annotations

from django.test import TestCase
from rest_framework.exceptions import Throttled
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from api.lockout import FailedAuthLockout
from api.tests.helpers import with_throttle_rate
from common.throttle_scopes import ThrottleScope


class FailedAuthLockoutTests(TestCase):
    def _throttle(self, ip: str = "1.1.1.1") -> FailedAuthLockout:
        return FailedAuthLockout(Request(APIRequestFactory().get("/", REMOTE_ADDR=ip)))

    @with_throttle_rate(ThrottleScope.FAILED_AUTH, "2/hour")
    def test_locks_out_an_ip_after_too_many_failures(self) -> None:
        self._throttle().record()
        self._throttle().check()
        self._throttle().record()

        with self.assertRaises(Throttled) as raised:
            self._throttle().check()
        self.assertGreater(raised.exception.wait, 0)
        self._throttle(ip="2.2.2.2").check()

    @with_throttle_rate(ThrottleScope.FAILED_AUTH, "2/hour")
    def test_checking_alone_never_locks_anyone_out(self) -> None:
        for _ in range(10):
            self._throttle().check()
