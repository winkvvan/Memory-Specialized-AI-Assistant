"""Restart persistence: all state must survive a full rebuild (process restart)."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from chris.app import build_runtime
from chris.config import Config
from chris.llm.mock import MockProvider
from chris.memory.base import MemoryEntry
from chris.personality.models import PersonalityChangeRecord


def _make_config(tmp: str) -> Config:
    config = Config()
    config.personality_file = str(Path(tmp) / "p.json")
    config.memory_file = str(Path(tmp) / "m.json")
    config.temporal_file = str(Path(tmp) / "t.json")
    config.world_state_file = str(Path(tmp) / "w.json")
    return config


class TestRestartPersistence(unittest.TestCase):
    def test_full_state_restored_after_rebuild(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _make_config(tmp)
            base = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)

            session_one = build_runtime(config, provider=MockProvider())
            session_one.temporal.start_session()
            session_one.memory.add(MemoryEntry(kind="fact", content="user likes coffee"))
            for days in (0, 5, 10):
                session_one.personality_store.propose(
                    PersonalityChangeRecord(
                        timestamp=base + timedelta(days=days),
                        reason="obs",
                        evidence="obs",
                        before={},
                        after={"value": "more"},
                        confidence=0.9,
                        field_name="humor_preference",
                    )
                )
            session_one.personality_store.run_evolution()
            session_one.personality = session_one.personality_store.current()
            session_one.world.update(location="office")
            session_one.temporal.end_session()

            session_two = build_runtime(config, provider=MockProvider())

            self.assertEqual(session_two.personality.version, 2)
            self.assertEqual(
                session_two.personality.adaptive.preferences["humor_preference"],
                {"value": "more"},
            )
            memory = session_two.memory.search("coffee")
            self.assertEqual(len(memory), 1)
            self.assertIsNotNone(session_two.temporal.to_dict()["known_since"])
            self.assertEqual(session_two.world.location, "office")


if __name__ == "__main__":
    unittest.main()
