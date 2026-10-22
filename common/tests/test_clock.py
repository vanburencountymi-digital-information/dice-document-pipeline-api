from datetime import timedelta

from django.test import SimpleTestCase
from django.utils import timezone
from parameterized import parameterized

from common.clock import seconds_until


class SecondsUntilTests(SimpleTestCase):
    @parameterized.expand(
        [("future", 90, 90), ("part_second_rounds_up", 90.4, 91), ("past", -30, 0)]
    )
    def test_seconds_until(self, _label: str, offset: float, expected: int) -> None:
        moment = timezone.now() + timedelta(seconds=offset)

        # a few milliseconds pass between making `moment` and measuring it
        self.assertIn(seconds_until(moment), {expected, expected - 1} if expected else {0})
