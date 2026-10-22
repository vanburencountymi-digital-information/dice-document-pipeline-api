from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.exceptions import Throttled

from accounts.models import IncompleteLimitsError, ServiceAccount
from accounts.tests.factories import ServiceAccountFactory
from api.submission_limits import NoSubmissionLimit, SubmissionLimit, limit_for
from remediation.models import Remediation
from remediation.tests.factories import RemediationFactory


def _submit(account: ServiceAccount, age: timedelta = timedelta(0)) -> Remediation:
    remediation = RemediationFactory(
        service_account=account, status=Remediation.JobStatus.COMPLIANT
    )
    Remediation.objects.filter(pk=remediation.pk).update(created_at=timezone.now() - age)
    return remediation


def _limited(account: ServiceAccount) -> SubmissionLimit:
    throttle = limit_for(account)
    assert isinstance(throttle, SubmissionLimit)
    return throttle


class SubmissionLimitTests(TestCase):
    def test_allows_until_the_limit_of_jobs_is_reached(self) -> None:
        account = ServiceAccountFactory(rate_limit_count=2, rate_limit_period="day")
        throttle = limit_for(account)

        throttle.check()
        _submit(account)
        throttle.check()
        _submit(account)

        with self.assertRaises(Throttled) as raised:
            throttle.check()
        self.assertEqual(raised.exception.detail["limit"], "rate")
        self.assertEqual(raised.exception.detail["remaining"], 0)
        self.assertEqual(raised.exception.detail["retry_after"], raised.exception.wait)
        self.assertIn("resets_at", raised.exception.detail)
        self.assertGreater(raised.exception.wait, 0)

    def test_checking_does_not_use_up_the_limit(self) -> None:
        throttle = limit_for(ServiceAccountFactory(rate_limit_count=1, rate_limit_period="day"))

        for _ in range(5):
            throttle.check()

    def test_each_account_has_its_own_count(self) -> None:
        busy = ServiceAccountFactory(rate_limit_count=1, rate_limit_period="day")
        quiet = ServiceAccountFactory(rate_limit_count=1, rate_limit_period="day")
        _submit(busy)

        limit_for(quiet).check()
        with self.assertRaises(Throttled):
            limit_for(busy).check()

    def test_a_job_stops_counting_one_period_after_it_was_made(self) -> None:
        account = ServiceAccountFactory(rate_limit_count=1, rate_limit_period="hour")
        _submit(account, age=timedelta(minutes=61))

        limit_for(account).check()

    def test_wait_is_how_long_until_the_oldest_job_stops_counting(self) -> None:
        account = ServiceAccountFactory(rate_limit_count=1, rate_limit_period="hour")
        _submit(account, age=timedelta(minutes=50))

        with self.assertRaises(Throttled) as raised:
            limit_for(account).check()

        self.assertAlmostEqual(raised.exception.wait, 10 * 60, delta=5)

    def test_a_limit_of_zero_blocks_everything(self) -> None:
        account = ServiceAccountFactory(rate_limit_count=0, rate_limit_period="day")

        with self.assertRaises(Throttled) as raised:
            limit_for(account).check()

        self.assertEqual(raised.exception.wait, 24 * 3600)
        self.assertEqual(_limited(account).headers()["X-RateLimit-Remaining"], "0")

    def test_a_limit_of_zero_gives_the_same_reset_in_the_headers_and_the_429(self) -> None:
        account = ServiceAccountFactory(rate_limit_count=0, rate_limit_period="day")
        throttle = _limited(account)

        with self.assertRaises(Throttled) as raised:
            throttle.check()

        reset = int(throttle.headers()["X-RateLimit-Reset"])
        self.assertEqual(reset, 24 * 3600)
        self.assertEqual(reset, raised.exception.wait)

    def test_changing_the_limit_takes_effect_straight_away(self) -> None:
        account = ServiceAccountFactory(rate_limit_count=1, rate_limit_period="day")
        _submit(account)
        account.rate_limit_count = 2

        limit_for(account).check()

    def test_headers_report_limit_remaining_and_reset(self) -> None:
        account = ServiceAccountFactory(rate_limit_count=5, rate_limit_period="hour")
        _submit(account)

        headers = _limited(account).headers()

        self.assertEqual(headers["X-RateLimit-Limit"], "5")
        self.assertEqual(headers["X-RateLimit-Remaining"], "4")
        self.assertAlmostEqual(int(headers["X-RateLimit-Reset"]), 3600, delta=5)

    def test_headers_with_no_jobs_reset_in_zero(self) -> None:
        headers = _limited(ServiceAccountFactory()).headers()

        self.assertEqual(
            headers,
            {"X-RateLimit-Limit": "100", "X-RateLimit-Remaining": "100", "X-RateLimit-Reset": "0"},
        )


class LimitForTests(TestCase):
    def test_unlimited_account_gets_no_limit(self) -> None:
        throttle = limit_for(ServiceAccountFactory(unlimited=True))

        self.assertIsInstance(throttle, NoSubmissionLimit)
        for _ in range(3):
            throttle.check()
        self.assertEqual(throttle.headers(), {})

    def test_incomplete_limits_are_an_error_not_unlimited(self) -> None:
        account = ServiceAccountFactory.build(rate_limit_count=None, unlimited=False)

        with self.assertRaises(IncompleteLimitsError):
            limit_for(account)

    def test_limited_account_gets_a_real_throttle(self) -> None:
        self.assertIsInstance(limit_for(ServiceAccountFactory()), SubmissionLimit)
