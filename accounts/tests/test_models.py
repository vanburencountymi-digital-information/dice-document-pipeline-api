from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase, override_settings
from parameterized import parameterized
from rest_framework.throttling import SimpleRateThrottle

from accounts.models import LIMIT_FIELDS, IncompleteLimitsError, RateLimitPeriod, ServiceAccount
from accounts.tests.factories import ServiceAccountFactory, UserFactory


class ServiceAccountLimitsTests(SimpleTestCase):
    @override_settings(DEFAULT_MAX_IN_FLIGHT=25, DEFAULT_RATE_LIMIT_COUNT=200)
    def test_new_account_defaults_come_from_settings(self) -> None:
        account = ServiceAccount()

        self.assertEqual(account.max_in_flight, 25)
        self.assertEqual(account.rate_limit_count, 200)
        self.assertEqual(account.rate_limit_period, RateLimitPeriod.DAY)

    @parameterized.expand(
        [
            ("second", 2, "second", "2/second"),
            ("minute", 5, "minute", "5/minute"),
            ("day", 100, "day", "100/day"),
        ]
    )
    def test_rate_limit_is_written_the_way_drf_reads_it(
        self, _label: str, count: int, period: str, expected: str
    ) -> None:
        account = ServiceAccount(rate_limit_count=count, rate_limit_period=period)

        self.assertEqual(account.rate_limit, expected)

    def test_in_flight_cap_is_the_stored_value(self) -> None:
        self.assertEqual(ServiceAccount(max_in_flight=7).in_flight_cap, 7)

    @parameterized.expand(
        [
            ("all_values_kept", 5, 100, "day"),
            ("all_values_blank", None, None, None),
        ]
    )
    def test_unlimited_means_no_limits_whatever_the_values(
        self, _label: str, max_in_flight: int | None, count: int | None, period: str | None
    ) -> None:
        account = ServiceAccount(
            unlimited=True,
            max_in_flight=max_in_flight,
            rate_limit_count=count,
            rate_limit_period=period,
        )

        self.assertIsNone(account.in_flight_cap)
        self.assertIsNone(account.rate_limit)

    @parameterized.expand(
        [
            ("count_missing", None, "day"),
            ("period_missing", 100, None),
            ("both_missing", None, None),
        ]
    )
    def test_missing_rate_limit_without_unlimited_is_an_error_not_unlimited(
        self, _label: str, count: int | None, period: str | None
    ) -> None:
        account = ServiceAccount(rate_limit_count=count, rate_limit_period=period)

        with self.assertRaises(IncompleteLimitsError):
            _ = account.rate_limit

    def test_missing_in_flight_cap_without_unlimited_is_an_error_not_unlimited(self) -> None:
        with self.assertRaises(IncompleteLimitsError):
            _ = ServiceAccount(max_in_flight=None).in_flight_cap


class ServiceAccountForUserTests(TestCase):
    def test_returns_the_users_service_account(self) -> None:
        account = ServiceAccountFactory()

        self.assertEqual(ServiceAccount.for_user(account.user), account)

    def test_returns_none_for_a_user_with_no_service_account(self) -> None:
        self.assertIsNone(ServiceAccount.for_user(UserFactory()))

    def test_returns_none_for_an_anonymous_user(self) -> None:
        self.assertIsNone(ServiceAccount.for_user(AnonymousUser()))


class ServiceAccountIsUnlimitedTests(SimpleTestCase):
    @parameterized.expand(
        [
            ("ticked", True, None, True),
            ("ticked_with_values", True, 5, True),
            ("not_ticked", False, 5, False),
            ("not_ticked_and_half_filled_is_not_unlimited", False, None, False),
        ]
    )
    def test_is_unlimited_is_only_the_ticked_box(
        self, _label: str, unlimited: bool, in_flight: int | None, expected: bool
    ) -> None:
        account = ServiceAccount(unlimited=unlimited, max_in_flight=in_flight)

        self.assertEqual(account.is_unlimited, expected)


class LimitFieldsTests(SimpleTestCase):
    def test_limit_fields_are_every_model_field_that_is_not_identity_or_credentials(self) -> None:
        others = {"id", "organization", "name", "user", "webhook_secret"}
        # Concrete only: reverse relations (tokens, remediation jobs) aren't fields to set
        model_fields = {field.name for field in ServiceAccount._meta.concrete_fields}

        self.assertEqual(set(LIMIT_FIELDS), model_fields - others)


class RateLimitPeriodTests(SimpleTestCase):
    @parameterized.expand(
        [("second", 1), ("minute", 60), ("hour", 3600), ("day", 86400)],
    )
    def test_every_period_is_understood_by_drf(self, period: str, seconds: int) -> None:
        class Throttle(SimpleRateThrottle):
            rate = "1/day"

            def get_cache_key(self, request, view):
                return None

        self.assertIn(period, RateLimitPeriod.values)
        self.assertEqual(Throttle().parse_rate(f"3/{period}"), (3, seconds))

    def test_every_period_is_tested(self) -> None:
        self.assertEqual(set(RateLimitPeriod.values), {"second", "minute", "hour", "day"})


class ServiceAccountSaveValidationTests(TestCase):
    @parameterized.expand(
        [
            ("in_flight_missing", {"max_in_flight": None}),
            ("count_missing", {"rate_limit_count": None}),
            ("period_missing", {"rate_limit_period": None}),
            (
                "all_missing",
                {"max_in_flight": None, "rate_limit_count": None, "rate_limit_period": None},
            ),
            ("in_flight_negative", {"max_in_flight": -1}),
            ("count_negative", {"rate_limit_count": -1}),
            ("unknown_period", {"rate_limit_period": "week"}),
        ]
    )
    def test_bad_limits_cannot_be_saved(self, _label: str, bad_values: dict) -> None:
        with self.assertRaises(ValidationError):
            ServiceAccountFactory(**bad_values)

        self.assertFalse(ServiceAccount.objects.exists())

    def test_zero_is_a_valid_limit_that_blocks_everything(self) -> None:
        account = ServiceAccountFactory(max_in_flight=0, rate_limit_count=0)

        account.refresh_from_db()
        self.assertEqual(account.in_flight_cap, 0)
        self.assertEqual(account.rate_limit, "0/day")

    def test_blank_limits_are_fine_when_unlimited(self) -> None:
        account = ServiceAccountFactory(
            unlimited=True, max_in_flight=None, rate_limit_count=None, rate_limit_period=None
        )

        account.refresh_from_db()
        self.assertIsNone(account.in_flight_cap)
        self.assertIsNone(account.rate_limit)

    def test_unticking_unlimited_on_blank_values_cannot_be_saved(self) -> None:
        account = ServiceAccountFactory(unlimited=True, max_in_flight=None)
        account.unlimited = False

        with self.assertRaises(ValidationError):
            account.save(update_fields=["unlimited"])

        account.refresh_from_db()
        self.assertTrue(account.unlimited)

    def test_the_database_still_refuses_blank_limits_that_skip_save(self) -> None:
        account = ServiceAccountFactory()

        with self.assertRaises(IntegrityError), transaction.atomic():
            ServiceAccount.objects.filter(pk=account.pk).update(max_in_flight=None)
