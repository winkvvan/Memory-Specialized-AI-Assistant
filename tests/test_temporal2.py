"""Temporal Engine 2.0: continuous timeline, offline compensation, layered
checks, manual simulation and provider independence."""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from chris.events import EventBus, EventType
from chris.temporal.clock import AcceleratedClock, ManualClock, RealClock
from chris.temporal.system import TemporalSystem

BASE = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)


class _Bus:
    def __init__(self):
        self.events = []
        self.bus = EventBus()
        self.bus.subscribe(self.events.append)

    def types(self):
        return [e.type for e in self.events]


class TestClockReplaceable(unittest.TestCase):
    def test_clock_mode_detection(self):
        self.assertEqual(TemporalSystem(clock=RealClock()).clock_mode(), "real")
        self.assertEqual(TemporalSystem(clock=ManualClock(BASE)).clock_mode(), "manual")
        self.assertEqual(
            TemporalSystem(clock=AcceleratedClock(scale=60)).clock_mode(), "accelerated"
        )

    def test_switch_clock(self):
        system = TemporalSystem(clock=ManualClock(BASE))
        system.switch_clock("real")
        self.assertEqual(system.clock_mode(), "real")
        system.switch_clock("manual")
        self.assertEqual(system.clock_mode(), "manual")


class TestManualSimulation(unittest.TestCase):
    def test_advance_generates_time_passed_and_day_changed(self):
        bus = _Bus()
        clock = ManualClock(BASE)
        system = TemporalSystem(clock=clock, event_bus=bus.bus)
        system.start_session()
        system.advance(timedelta(days=7))
        types = bus.types()
        self.assertIn(EventType.TIME_PASSED, types)
        self.assertIn(EventType.DAY_CHANGED, types)

    def test_advance_only_on_manual_clock(self):
        system = TemporalSystem(clock=RealClock())
        self.assertFalse(system.advance(timedelta(days=1)))


class TestOfflineCompensation(unittest.TestCase):
    def test_30_day_absence_emits_skip_and_long_absence(self):
        bus = _Bus()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.json"
            clock = ManualClock(BASE)
            system = TemporalSystem(path=path, clock=clock, event_bus=bus.bus)
            system.start_session()
            system.end_session()  # last_shutdown = BASE
            clock.advance(timedelta(days=30))  # computer off for 30 days
            reloaded = TemporalSystem(path=path, clock=clock, event_bus=bus.bus)
            reloaded.start_session()
            types = bus.types()
            self.assertIn(EventType.TIME_SKIPPED, types)
            self.assertIn(EventType.LONG_ABSENCE, types)

    def test_abnormal_shutdown_does_not_lose_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.json"
            clock = ManualClock(BASE)
            system = TemporalSystem(path=path, clock=clock)
            system.start_session()  # writes known_since / session_start, no end_session
            clock.advance(timedelta(days=2))
            reloaded = TemporalSystem(path=path, clock=clock)
            self.assertEqual(reloaded.days_known(), 2)
            self.assertIsNotNone(reloaded._known_since)


class TestPersistence(unittest.TestCase):
    def test_restart_restores_time_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.json"
            clock = ManualClock(BASE)
            system = TemporalSystem(path=path, clock=clock)
            system.start_session()
            clock.advance(timedelta(days=5))
            system.end_session()
            reloaded = TemporalSystem(path=path, clock=clock)
            self.assertEqual(reloaded.days_known(), 5)
            self.assertIsNotNone(reloaded._last_shutdown)
            self.assertEqual(reloaded._last_shutdown, BASE + timedelta(days=5))


class TestEvents(unittest.TestCase):
    def test_event_bus_receives_time_events(self):
        bus = _Bus()
        clock = ManualClock(BASE)
        system = TemporalSystem(clock=clock, event_bus=bus.bus)
        system.start_session()
        clock.advance(timedelta(days=2))
        system.advance(timedelta(days=2))
        self.assertIn(EventType.SESSION_STARTED, bus.types())
        self.assertIn(EventType.TIME_PASSED, bus.types())

    def test_idle_and_long_events(self):
        bus = _Bus()
        clock = ManualClock(BASE)
        system = TemporalSystem(clock=clock, event_bus=bus.bus)
        system.start_session()
        clock.advance(timedelta(minutes=90))
        system.checkpoint()
        types = bus.types()
        self.assertIn(EventType.IDLE_STARTED, types)
        self.assertIn(EventType.LONG_IDLE, types)
        self.assertIn(EventType.LONG_SESSION, types)

    def test_idle_ends_on_interaction(self):
        bus = _Bus()
        clock = ManualClock(BASE)
        system = TemporalSystem(clock=clock, event_bus=bus.bus)
        system.start_session()
        clock.advance(timedelta(minutes=90))
        system.record_interaction()
        self.assertIn(EventType.IDLE_ENDED, bus.types())

    def test_day_week_month_changed(self):
        bus = _Bus()
        clock = ManualClock(BASE)
        system = TemporalSystem(clock=clock, event_bus=bus.bus)
        system.start_session()
        clock.advance(timedelta(days=33))
        system.checkpoint()
        types = bus.types()
        self.assertIn(EventType.DAY_CHANGED, types)
        self.assertIn(EventType.WEEK_CHANGED, types)
        self.assertIn(EventType.MONTH_CHANGED, types)

    def test_threshold_reached(self):
        bus = _Bus()
        clock = ManualClock(BASE)
        system = TemporalSystem(clock=clock, event_bus=bus.bus, threshold_hours=(1,))
        system.start_session()
        clock.advance(timedelta(hours=2))
        system.record_interaction()
        self.assertIn(EventType.TIME_THRESHOLD_REACHED, bus.types())

    def test_ticker_emits_time_tick(self):
        import time

        system = TemporalSystem()
        system.start_ticker(interval=0.05)
        time.sleep(0.3)
        system.stop_ticker()
        types = {e["type"] for e in system.recent_events(limit=200)}
        self.assertIn(EventType.TIME_TICK.value, types)


class TestProviderIndependence(unittest.TestCase):
    def test_switching_provider_keeps_time_state(self):
        from chris.app import build_runtime
        from chris.config import Config
        from chris.llm.mock import MockProvider

        with tempfile.TemporaryDirectory() as tmp:
            config = Config()
            config.temporal_file = str(Path(tmp) / "t.json")
            config.memory_file = str(Path(tmp) / "m.json")
            config.personality_file = str(Path(tmp) / "p.json")
            config.world_state_file = str(Path(tmp) / "w.json")

            a = build_runtime(config, provider=MockProvider(responses=["A"]))
            a.temporal.start_session()
            state_a = a.temporal.to_dict()

            b = build_runtime(config, provider=MockProvider(responses=["B", "C"]))
            self.assertEqual(b.temporal.to_dict()["known_since"], state_a["known_since"])


class TestPromptProjection(unittest.TestCase):
    def test_prompt_summary_minimal(self):
        clock = ManualClock(BASE)
        system = TemporalSystem(clock=clock)
        system.start_session()
        summary = system.prompt_summary()
        self.assertIn("Current time:", summary)
        for secret in ("crossed_thresholds", "last_shutdown_time", "threshold_hours"):
            self.assertNotIn(secret, summary)

    def test_memory_ecology_interfaces(self):
        clock = ManualClock(BASE)
        system = TemporalSystem(clock=clock)
        created = BASE - timedelta(days=10)
        self.assertEqual(system.get_age(created), timedelta(days=10))
        self.assertEqual(system.get_elapsed(created), timedelta(days=10))
        factor = system.calculate_time_factor(created, half_life=864000)
        self.assertAlmostEqual(factor, 0.5, places=6)
        self.assertEqual(system.calculate_time_factor(BASE, half_life=0), 1.0)

    def test_humanize_age(self):
        clock = ManualClock(BASE)
        system = TemporalSystem(clock=clock)
        self.assertEqual(system.humanize_age(BASE), "刚刚")
        self.assertEqual(system.humanize_age(BASE - timedelta(minutes=15)), "15分钟前")
        self.assertEqual(system.humanize_age(BASE - timedelta(hours=5)), "5小时前")
        self.assertEqual(system.humanize_age(BASE - timedelta(days=3)), "3天前")
        self.assertEqual(system.humanize_age(BASE - timedelta(days=400)), "1年前")

    def test_known_duration_granularity(self):
        clock = ManualClock(BASE)
        system = TemporalSystem(clock=clock)
        system._known_since = BASE - timedelta(hours=22)
        self.assertEqual(system.known_duration(), "22 hours")
        clock.advance(timedelta(days=1))
        self.assertEqual(system.known_duration(), "1 days")

    def test_session_active_reflects_start(self):
        clock = ManualClock(BASE)
        system = TemporalSystem(clock=clock)
        self.assertFalse(system.session_active())
        system.start_session()
        self.assertTrue(system.session_active())
        system.end_session()
        self.assertFalse(system.session_active())

    def test_time_context_authoritative(self):
        clock = ManualClock(BASE)
        system = TemporalSystem(clock=clock)
        system._known_since = BASE - timedelta(hours=30)
        system._last_interaction = BASE - timedelta(minutes=5)
        context = "\n".join(system.time_context())
        self.assertIn("Current time:", context)
        self.assertIn("known the user for", context)
        self.assertIn("Last interaction", context)
        self.assertIn("absolute timestamps", context)


if __name__ == "__main__":
    unittest.main()
