"""State independence: Chris's state must not depend on which provider is used.

Build a runtime with provider A, mutate all state, then build a new runtime with
provider B against the SAME data files. Every state object must be identical.
"""

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
from chris.events import utcnow


def _make_config(tmp: str) -> Config:
    config = Config()
    config.personality_file = str(Path(tmp) / "p.json")
    config.memory_file = str(Path(tmp) / "m.json")
    config.temporal_file = str(Path(tmp) / "t.json")
    config.world_state_file = str(Path(tmp) / "w.json")
    return config


def _populate(runtime) -> None:
    runtime.temporal.start_session()
    runtime.temporal.record_interaction()
    runtime.memory.add(MemoryEntry(kind="fact", content="user likes coffee", metadata={"tags": ["pref"]}))
    base = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    for days in (0, 5, 10):
        runtime.personality_store.propose(
            PersonalityChangeRecord(
                timestamp=base + timedelta(days=days),
                reason="observation",
                evidence="observation",
                before={},
                after={"value": "more"},
                confidence=0.9,
                field_name="humor_preference",
            )
        )
    runtime.personality_store.run_evolution()
    runtime.personality = runtime.personality_store.current()
    runtime.world.update(location="home", current_activity="working")


class TestStateIndependentOfProvider(unittest.TestCase):
    def test_swap_provider_preserves_every_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _make_config(tmp)

            provider_a = MockProvider(responses=["model A reply"])
            runtime_a = build_runtime(config, provider=provider_a)
            _populate(runtime_a)

            provider_b = MockProvider(responses=["model B reply", "different"])
            runtime_b = build_runtime(config, provider=provider_b)

            self.assertEqual(runtime_b.personality.version, runtime_a.personality.version)
            self.assertEqual(
                runtime_b.personality.adaptive.preferences,
                runtime_a.personality.adaptive.preferences,
            )
            self.assertEqual(runtime_b.personality.core.to_dict(), runtime_a.personality.core.to_dict())

            memory_a = [e.to_dict() for e in runtime_a.memory.list_recent(limit=100)]
            memory_b = [e.to_dict() for e in runtime_b.memory.list_recent(limit=100)]
            self.assertEqual(memory_b, memory_a)

            self.assertEqual(
                runtime_b.temporal.to_dict()["known_since"],
                runtime_a.temporal.to_dict()["known_since"],
            )
            self.assertEqual(runtime_b.temporal.days_known(), runtime_a.temporal.days_known())
            self.assertEqual(runtime_b.world.to_dict(), runtime_a.world.to_dict())


if __name__ == "__main__":
    unittest.main()
