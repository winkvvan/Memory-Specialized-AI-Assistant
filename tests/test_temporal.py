import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from chris.events import EventBus, EventType
from chris.temporal.system import TemporalSystem

START = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)


class FakeClock:
    def __init__(self):
        self.current = START

    def __call__(self):
        return self.current

    def advance(self, **kwargs):
        self.current += timedelta(**kwargs)


class TestTemporalSystem(unittest.TestCase):
    def test_start_session_sets_known_since_and_uptime(self):
        clock = FakeClock()
        temporal = TemporalSystem(clock=clock)
        temporal.start_session()
        self.assertEqual(temporal.days_known(), 0)
        clock.advance(hours=2)
        self.assertEqual(temporal.session_uptime(), timedelta(hours=2))

    def test_time_of_day_in_utc_timezone(self):
        clock = FakeClock()
        temporal = TemporalSystem(clock=clock, timezone="UTC")
        self.assertEqual(temporal.time_of_day(), "morning")
        clock.current = datetime(2026, 1, 1, 20, 0, tzinfo=timezone.utc)
        self.assertEqual(temporal.time_of_day(), "evening")
        clock.current = datetime(2026, 1, 1, 2, 0, tzinfo=timezone.utc)
        self.assertEqual(temporal.time_of_day(), "night")

    def test_local_time_uses_configured_timezone(self):
        clock = FakeClock()
        temporal = TemporalSystem(clock=clock, timezone="Asia/Shanghai")
        # 05:00 UTC == 13:00 in Asia/Shanghai -> afternoon, not "凌晨五点"
        clock.current = datetime(2026, 1, 1, 5, 0, tzinfo=timezone.utc)
        self.assertEqual(temporal.time_of_day(), "afternoon")
        self.assertIn("Asia/Shanghai", temporal.prompt_summary())
        self.assertIn("13:00", temporal.prompt_summary())

    def test_threshold_event_emitted_once(self):
        clock = FakeClock()
        bus = EventBus()
        seen = []
        bus.subscribe(lambda e: seen.append(e), EventType.TIME_THRESHOLD_REACHED)
        temporal = TemporalSystem(event_bus=bus, threshold_hours=(1,), clock=clock)
        temporal.start_session()
        clock.advance(hours=2)
        temporal.record_interaction()
        clock.advance(hours=3)
        temporal.record_interaction()
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].payload["hours"], 1)

    def test_persistence(self):
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "temporal.json"
            temporal = TemporalSystem(path=path, clock=clock)
            temporal.start_session()
            reloaded = TemporalSystem(path=path, clock=clock)
            reloaded.start_session()
            self.assertEqual(reloaded.days_known(), 0)

    def test_days_known(self):
        clock = FakeClock()
        temporal = TemporalSystem(clock=clock)
        temporal.start_session()
        clock.advance(days=3)
        self.assertEqual(temporal.days_known(), 3)


if __name__ == "__main__":
    unittest.main()
