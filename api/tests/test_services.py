from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from api.models import ThrottleCounter
from api.services import ThrottleCounterService


class ThrottleCounterServiceTests(TestCase):
    def setUp(self) -> None:
        self.service = ThrottleCounterService()

    def test_counts_hits_within_a_window(self) -> None:
        readings = [self.service.hit("ip:1.1.1.1", 60) for _ in range(3)]

        self.assertEqual([r.count for r in readings], [1, 2, 3])
        self.assertLessEqual(readings[-1].seconds_left, 60)
        self.assertGreater(readings[-1].seconds_left, 0)

    def test_keys_are_counted_separately(self) -> None:
        self.service.hit("ip:1.1.1.1", 60)
        self.service.hit("ip:1.1.1.1", 60)

        self.assertEqual(self.service.hit("ip:2.2.2.2", 60).count, 1)

    def test_a_new_window_starts_after_the_old_one_ends(self) -> None:
        self.service.hit("ip:1.1.1.1", 60)
        self.service.hit("ip:1.1.1.1", 60)
        ThrottleCounter.objects.update(window_ends_at=timezone.now() - timedelta(seconds=1))

        reading = self.service.hit("ip:1.1.1.1", 60)

        self.assertEqual(reading.count, 1)
        self.assertEqual(ThrottleCounter.objects.count(), 1)

    def test_read_does_not_count(self) -> None:
        self.assertEqual(self.service.read("ip:1.1.1.1").count, 0)

        self.service.hit("ip:1.1.1.1", 60)

        self.assertEqual(self.service.read("ip:1.1.1.1").count, 1)
        self.assertEqual(self.service.read("ip:1.1.1.1").count, 1)

    def test_an_expired_row_reads_as_zero(self) -> None:
        self.service.hit("ip:1.1.1.1", 60)
        ThrottleCounter.objects.update(window_ends_at=timezone.now() - timedelta(seconds=1))

        self.assertEqual(self.service.read("ip:1.1.1.1").count, 0)

    def test_a_clash_when_creating_the_row_is_retried(self) -> None:
        real_create = ThrottleCounter.objects.create
        attempts: list[int] = []

        def clash_once(**kwargs):
            attempts.append(1)
            if len(attempts) == 1:
                raise IntegrityError("duplicate")
            return real_create(**kwargs)

        with patch.object(ThrottleCounter.objects, "create", side_effect=clash_once):
            reading = self.service.hit("ip:1.1.1.1", 60)

        self.assertEqual(reading.count, 1)
        self.assertEqual(len(attempts), 2)

    def test_gives_up_if_the_row_keeps_clashing(self) -> None:
        with (
            patch.object(ThrottleCounter.objects, "create", side_effect=IntegrityError("dup")),
            self.assertRaises(RuntimeError),
        ):
            self.service.hit("ip:1.1.1.1", 60)

    def test_old_expired_rows_are_removed_when_a_new_row_is_made(self) -> None:
        ThrottleCounter.objects.create(
            key="ip:old", count=5, window_ends_at=timezone.now() - timedelta(hours=2)
        )

        self.service.hit("ip:new", 60)

        self.assertFalse(ThrottleCounter.objects.filter(key="ip:old").exists())
