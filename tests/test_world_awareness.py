"""World Awareness 2.0 tests: schema, presence/activity/attention inference,
events (no duplicate on no change), activity timeline, temporal integration,
mock + windows sensor, persistence, provider independence."""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from chris.events import EventBus, EventType
from chris.temporal.clock import ManualClock
from chris.temporal.system import TemporalSystem
from chris.world.awareness import WorldAwareness
from chris.world.sensors import MockWorldSensor, WindowsComputerAdapter, create_sensor
from chris.world.state import (
    UserActivity,
    UserAttention,
    UserBusyState,
    UserPresence,
    WorldState,
    load_world_state,
    save_world_state,
)

BASE = datetime(2026, 8, 10, 20, 0, 0, tzinfo=timezone.utc)


def make_awareness():
    tmp = tempfile.TemporaryDirectory()
    clock = ManualClock(BASE)
    temporal = TemporalSystem(clock=clock)
    bus = EventBus()
    seen = []
    bus.subscribe(seen.append)
    awareness = WorldAwareness(
        state=WorldState(),
        sensor=MockWorldSensor(),
        path=Path(tmp.name) / "world.json",
        event_bus=bus,
        temporal=temporal,
    )
    return awareness, clock, seen, tmp


def types(seen):
    return [e.type for e in seen]


class TestWorldStateSchema(unittest.TestCase):
    def test_initial_state(self):
        state = WorldState()
        self.assertEqual(state.location, "unknown")
        self.assertEqual(state.current_activity, "idle")
        self.assertFalse(state.user_presence)
        self.assertEqual(state.user.activity, UserActivity.IDLE)
        self.assertEqual(state.user.presence, UserPresence.UNKNOWN)

    def test_persistence_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "world.json"
            state = WorldState(location="office", current_activity="working")
            state.user.activity = UserActivity.CODING
            state.computer.foreground_app = "code.exe"
            save_world_state(state, path)
            restored = load_world_state(path)
            self.assertEqual(restored.location, "office")
            self.assertEqual(restored.user.activity, UserActivity.CODING)
            self.assertEqual(restored.computer.foreground_app, "code.exe")


class TestPresenceActivityAttention(unittest.TestCase):
    def test_presence_away(self):
        awareness, _clock, seen, tmp = make_awareness()
        awareness.ingest({"presence": "AWAY", "idle_seconds": 0, "active": False})
        self.assertEqual(awareness.state.user.presence, UserPresence.AWAY)
        self.assertFalse(awareness.state.user_presence)
        self.assertIn(EventType.USER_PRESENCE_CHANGED, types(seen))

    def test_activity_from_explicit(self):
        awareness, _clock, _seen, tmp = make_awareness()
        awareness.ingest({"activity": "CODING", "idle_seconds": 0})
        self.assertEqual(awareness.state.user.activity, UserActivity.CODING)

    def test_activity_inferred_from_foreground(self):
        awareness, _clock, _seen, tmp = make_awareness()
        awareness.ingest({"foreground_app": "chrome.exe", "window_title": "bilibili", "idle_seconds": 0})
        self.assertEqual(awareness.state.user.activity, UserActivity.BROWSING)

    def test_attention_focused_when_coding(self):
        awareness, _clock, _seen, tmp = make_awareness()
        awareness.ingest({"activity": "CODING", "idle_seconds": 5})
        self.assertEqual(awareness.state.user.attention, UserAttention.FOCUSED)

    def test_attention_relaxed_when_watching(self):
        awareness, _clock, _seen, tmp = make_awareness()
        awareness.ingest({"activity": "WATCHING", "idle_seconds": 120})
        self.assertEqual(awareness.state.user.attention, UserAttention.RELAXED)

    def test_idle_does_not_imply_away_with_video(self):
        awareness, _clock, _seen, tmp = make_awareness()
        awareness.ingest({"foreground_app": "vlc.exe", "idle_seconds": 300, "active": True})
        self.assertEqual(awareness.state.user.activity, UserActivity.WATCHING)
        self.assertEqual(awareness.state.user.estimated_state, UserBusyState.UNKNOWN)


class TestEvents(unittest.TestCase):
    def test_change_emits_events(self):
        awareness, _clock, seen, tmp = make_awareness()
        awareness.ingest({"activity": "CODING", "idle_seconds": 0})
        self.assertIn(EventType.USER_ACTIVITY_CHANGED, types(seen))
        self.assertIn(EventType.WORLD_STATE_CHANGED, types(seen))

    def test_no_change_no_duplicate_events(self):
        awareness, _clock, seen, tmp = make_awareness()
        awareness.ingest({"activity": "CODING", "idle_seconds": 0})
        count_before = len(seen)
        awareness.ingest({"activity": "CODING", "idle_seconds": 0})
        self.assertEqual(len(seen), count_before, "identical observation must not re-emit events")

    def test_foreground_app_changed(self):
        awareness, _clock, seen, tmp = make_awareness()
        awareness.ingest({"foreground_app": "code.exe", "window_title": "a.py", "idle_seconds": 0})
        self.assertIn(EventType.FOREGROUND_APP_CHANGED, types(seen))


class TestActivityTimeline(unittest.TestCase):
    def test_same_activity_merges_into_one_session(self):
        awareness, clock, _seen, tmp = make_awareness()
        awareness.ingest({"activity": "CODING", "idle_seconds": 0})
        clock.advance(timedelta(minutes=30))
        awareness.ingest({"activity": "CODING", "idle_seconds": 0})
        clock.advance(timedelta(minutes=30))
        awareness.ingest({"activity": "CODING", "idle_seconds": 0})
        self.assertEqual(len(awareness.timeline()), 1)

    def test_activity_switch_creates_new_session(self):
        awareness, clock, _seen, tmp = make_awareness()
        awareness.ingest({"activity": "CODING", "idle_seconds": 0})
        clock.advance(timedelta(minutes=30))
        awareness.ingest({"activity": "BROWSING", "idle_seconds": 0})
        clock.advance(timedelta(minutes=15))
        awareness.ingest({"activity": "GAMING", "idle_seconds": 0})
        sessions = awareness.timeline()
        self.assertEqual([s.activity for s in sessions], ["CODING", "BROWSING", "GAMING"])

    def test_duration_grows_with_virtual_clock(self):
        awareness, clock, _seen, tmp = make_awareness()
        awareness.ingest({"activity": "CODING", "idle_seconds": 0})
        clock.advance(timedelta(hours=2))
        awareness.ingest({"activity": "CODING", "idle_seconds": 0})
        self.assertEqual(awareness.activity_duration(), timedelta(hours=2))


class TestSimulation(unittest.TestCase):
    def test_simulate_with_duration(self):
        awareness, _clock, _seen, tmp = make_awareness()
        events = awareness.simulate(activity="CODING", attention="FOCUSED", presence="PRESENT", duration=7200)
        self.assertIn(EventType.USER_ACTIVITY_CHANGED, events)
        self.assertEqual(awareness.state.user.activity, UserActivity.CODING)
        self.assertEqual(awareness.activity_duration(), timedelta(hours=2))


class TestSensors(unittest.TestCase):
    def test_mock_sensor_poll(self):
        sensor = MockWorldSensor()
        obs = sensor.poll()
        self.assertIn("activity", obs)
        self.assertIn("presence", obs)

    def test_create_sensor(self):
        self.assertIsInstance(create_sensor("mock"), MockWorldSensor)
        self.assertIsInstance(create_sensor("windows"), WindowsComputerAdapter)

    def test_windows_adapter_polls_without_raising(self):
        adapter = WindowsComputerAdapter()
        obs = adapter.poll()
        self.assertIsInstance(obs, dict)
        self.assertIn("idle_seconds", obs)
        self.assertIn("foreground_app", obs)
        self.assertIn("active", obs)
        self.assertGreaterEqual(obs["idle_seconds"], 0)


class TestPersistence(unittest.TestCase):
    def test_restart_restores_state_and_sessions(self):
        from chris.world.state import save_world_state

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "world.json"
            clock = ManualClock(BASE)
            awareness = WorldAwareness(
                state=WorldState(),
                sensor=MockWorldSensor(),
                path=path,
                event_bus=EventBus(),
                temporal=TemporalSystem(clock=clock),
            )
            awareness._bus.subscribe(
                lambda _e: save_world_state(awareness.state, path),
                EventType.WORLD_STATE_CHANGED,
            )
            awareness.ingest({"activity": "CODING", "idle_seconds": 0})
            clock.advance(timedelta(hours=1))
            awareness.ingest({"activity": "BROWSING", "idle_seconds": 0})
            reloaded = WorldAwareness(
                state=load_world_state(path),
                sensor=MockWorldSensor(),
                path=path,
                temporal=TemporalSystem(clock=clock),
            )
            self.assertEqual(reloaded.state.user.activity, UserActivity.BROWSING)
            self.assertEqual([s.activity for s in reloaded.timeline()], ["CODING", "BROWSING"])


class TestProviderIndependence(unittest.TestCase):
    def test_world_state_unchanged_across_providers(self):
        from chris.app import build_runtime
        from chris.config import Config
        from chris.llm.mock import MockProvider

        with tempfile.TemporaryDirectory() as tmp:
            config = Config()
            config.world_state_file = str(Path(tmp) / "w.json")
            config.memory_file = str(Path(tmp) / "m.json")
            config.personality_file = str(Path(tmp) / "p.json")
            config.temporal_file = str(Path(tmp) / "t.json")
            a = build_runtime(config, provider=MockProvider(responses=["A"]))
            a.world_awareness.ingest({"activity": "CODING", "idle_seconds": 0})
            state_a = a.world.to_dict()
            b = build_runtime(config, provider=MockProvider(responses=["B", "C"]))
            self.assertEqual(b.world.to_dict(), state_a)


if __name__ == "__main__":
    unittest.main()
