from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase
from parameterized import parameterized

from api.rates import parse_rate, rate_for
from api.tests.helpers import with_throttle_rate
from common.throttle_scopes import ThrottleScope


class ParseRateTests(SimpleTestCase):
    @parameterized.expand(
        [
            ("second", "5/second", 5, 1),
            ("minute", "10/minute", 10, 60),
            ("day", "100/day", 100, 86400),
            ("zero", "0/hour", 0, 3600),
        ]
    )
    def test_reads_drf_rate_text(self, _label: str, text: str, requests: int, seconds: int) -> None:
        self.assertEqual(parse_rate(text), (requests, seconds))


class ParseRateErrorTests(SimpleTestCase):
    @parameterized.expand(
        [("no_slash", "100"), ("unknown_period", "100/fortnight"), ("not_a_number", "x/day")]
    )
    def test_unreadable_rates_are_an_error_not_no_limit(self, _label: str, text: str) -> None:
        with self.assertRaises((ImproperlyConfigured, ValueError, KeyError)):
            parse_rate(text)


class RateForTests(SimpleTestCase):
    @with_throttle_rate(ThrottleScope.CLIENT_IP, "7/minute")
    def test_reads_the_scopes_rate_from_settings(self) -> None:
        self.assertEqual(rate_for(ThrottleScope.CLIENT_IP), (7, 60))

    def test_every_scope_has_a_readable_rate_in_settings(self) -> None:
        for scope in ThrottleScope:
            with self.subTest(scope=scope):
                requests, seconds = rate_for(scope)
                self.assertGreater(requests, 0)
                self.assertGreater(seconds, 0)
