"""Prompt projection: only the minimal runtime projection of structured state
reaches the model. Personality, Temporal, World and Memory all appear;
bulk storage never does.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from chris.app import build_runtime, build_system_prompt
from chris.config import Config
from chris.llm.mock import MockProvider
from chris.memory.base import MemoryEntry


def _make_config(tmp: str) -> Config:
    config = Config()
    config.personality_file = str(Path(tmp) / "p.json")
    config.memory_file = str(Path(tmp) / "m.json")
    config.temporal_file = str(Path(tmp) / "t.json")
    config.world_state_file = str(Path(tmp) / "w.json")
    return config


class TestPromptProjection(unittest.TestCase):
    def _runtime(self, tmp: str):
        config = _make_config(tmp)
        runtime = build_runtime(config, provider=MockProvider())
        runtime.personality.core.name = "Chris"
        runtime.personality.core.base_character = "A steady, curious assistant."
        runtime.personality.core.humor_tendency = "light"
        runtime.world.location = "home"
        runtime.world.current_activity = "working"
        runtime.temporal._session_start = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)
        runtime.temporal._known_since = datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc)
        runtime.memory.add(MemoryEntry(kind="fact", content="user likes green tea"))
        return runtime

    def test_personality_projected(self):
        with tempfile.TemporaryDirectory() as tmp:
            prompt = build_system_prompt(self._runtime(tmp))
            self.assertIn("Chris", prompt)
            self.assertIn("中文（简体）", prompt)
            self.assertIn("light", prompt)

    def test_temporal_projected(self):
        with tempfile.TemporaryDirectory() as tmp:
            prompt = build_system_prompt(self._runtime(tmp))
            self.assertIn("Time of day:", prompt)
            self.assertIn("You have known the user for", prompt)

    def test_world_projected(self):
        with tempfile.TemporaryDirectory() as tmp:
            prompt = build_system_prompt(self._runtime(tmp))
            self.assertIn("home", prompt)
            self.assertIn("working", prompt)

    def test_memory_projected(self):
        with tempfile.TemporaryDirectory() as tmp:
            prompt = build_system_prompt(self._runtime(tmp))
            self.assertIn("user likes green tea", prompt)
            self.assertIn("Recent context", prompt)

    def test_memory_projected_with_absolute_timestamp(self):
        from datetime import datetime, timedelta, timezone
        import re

        with tempfile.TemporaryDirectory() as tmp:
            runtime = self._runtime(tmp)
            entry = runtime.memory.add(MemoryEntry(kind="fact", content="old fact"))
            entry.timestamp = datetime.now(timezone.utc) - timedelta(hours=5)
            prompt = build_system_prompt(runtime)
            self.assertIn("old fact", prompt)
            self.assertIn("Recent context", prompt)
            self.assertTrue(
                re.search(r"\(\d{2}-\d{2} \d{2}:\d{2}\) old fact", prompt),
                "recent context should carry an absolute timestamp",
            )

    def test_exclude_since_filters_current_session_memory(self):
        from datetime import datetime, timedelta, timezone

        with tempfile.TemporaryDirectory() as tmp:
            runtime = self._runtime(tmp)
            old = runtime.memory.add(MemoryEntry(kind="fact", content="old memory"))
            old.timestamp = datetime.now(timezone.utc) - timedelta(hours=1)
            session = runtime.memory.add(MemoryEntry(kind="fact", content="session memory"))
            session.timestamp = datetime.now(timezone.utc)
            prompt = build_system_prompt(
                runtime, exclude_since=session.timestamp - timedelta(microseconds=1)
            )
            self.assertIn("old memory", prompt)
            self.assertNotIn("session memory", prompt)

    def test_remembered_section_in_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = self._runtime(tmp)
            runtime.memory.add(MemoryEntry(kind="fact", content="我对象叫小明", metadata={"remember": True}))
            prompt = build_system_prompt(runtime)
            self.assertIn("Things the user explicitly asked you to remember", prompt)
            self.assertIn("我对象叫小明", prompt)

    def test_prompt_is_not_a_dump_of_personality_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            prompt = build_system_prompt(self._runtime(tmp))
            for marker in ('"version"', '"change_log"', '"pending"', '"history"', '"immutable_principles"'):
                self.assertNotIn(marker, prompt)


if __name__ == "__main__":
    unittest.main()
