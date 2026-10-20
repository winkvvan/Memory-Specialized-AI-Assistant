import tempfile
import unittest
from pathlib import Path

from chris.events import EventBus, EventType
from chris.world.state import WorldState, load_world_state, save_world_state


class TestWorldState(unittest.TestCase):
    def test_defaults(self):
        state = WorldState()
        self.assertEqual(state.location, "unknown")
        self.assertEqual(state.current_activity, "idle")
        self.assertFalse(state.user_presence)

    def test_update_emits_event(self):
        bus = EventBus()
        seen = []
        bus.subscribe(lambda e: seen.append(e), EventType.WORLD_STATE_CHANGED)
        state = WorldState()
        state._bus = bus
        state.update(location="home", user_presence=True)
        self.assertEqual(state.location, "home")
        self.assertTrue(state.user_presence)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].source, "world")

    def test_round_trip_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "world.json"
            state = WorldState(location="office", current_activity="working")
            save_world_state(state, path)
            restored = load_world_state(path)
            self.assertEqual(restored.location, "office")
            self.assertEqual(restored.current_activity, "working")

    def test_load_default_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = load_world_state(Path(tmp) / "world.json")
            self.assertEqual(state.location, "unknown")


if __name__ == "__main__":
    unittest.main()
