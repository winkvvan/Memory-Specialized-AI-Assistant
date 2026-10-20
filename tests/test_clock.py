import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from chris.temporal import clock as clock_mod
from chris.temporal.clock import (
    AcceleratedClock,
    Clock,
    ManualClock,
    RealClock,
    create_clock,
)


class TestRealClock(unittest.TestCase):
    def test_now_is_near_utcnow(self):
        before = clock_mod.utcnow()
        now = RealClock().now()
        self.assertTrue(before <= now)

    def test_timestamp_and_elapsed(self):
        clock = RealClock()
        self.assertTrue(clock.timestamp().endswith("+00:00"))
        self.assertIsInstance(clock.elapsed_since(clock.now() - timedelta(seconds=5)), timedelta)


class TestManualClock(unittest.TestCase):
    def test_advance(self):
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        clock = ManualClock(base)
        clock.advance(timedelta(days=7))
        self.assertEqual(clock.now(), base + timedelta(days=7))


class TestAcceleratedClock(unittest.TestCase):
    def test_scale_formula(self):
        with mock.patch("chris.temporal.clock.utcnow") as fake:
            fake.return_value = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
            acc = AcceleratedClock(scale=3600)  # 1 real second = 1 simulated hour
            fake.return_value = datetime(2026, 1, 1, 0, 0, 10, tzinfo=timezone.utc)
            self.assertEqual(acc.now(), datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc))

    def test_scale_property(self):
        self.assertEqual(AcceleratedClock(scale=86400).scale, 86400)


class TestClockFactory(unittest.TestCase):
    def test_create_clock(self):
        self.assertIsInstance(create_clock("real"), RealClock)
        self.assertIsInstance(create_clock("manual"), ManualClock)
        self.assertIsInstance(create_clock("accelerated", scale=60), AcceleratedClock)

    def test_all_clocks_are_clock(self):
        for clock in (RealClock(), ManualClock(), AcceleratedClock(scale=60)):
            self.assertIsInstance(clock, Clock)
            self.assertTrue(hasattr(clock, "now"))


if __name__ == "__main__":
    unittest.main()
