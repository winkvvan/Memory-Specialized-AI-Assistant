import tempfile
import unittest
from pathlib import Path

from chris.events import EventBus, EventType
from chris.memory.base import MemoryEntry
from chris.memory.simple import SimpleFileMemory


class TestSimpleFileMemory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "memory.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_add_get_delete(self):
        memory = SimpleFileMemory(self.path)
        entry = memory.add(MemoryEntry(kind="fact", content="user likes coffee", importance=0.9))
        self.assertEqual(memory.get(entry.memory_id).content, "user likes coffee")
        self.assertTrue(memory.delete(entry.memory_id))
        self.assertIsNone(memory.get(entry.memory_id))

    def test_persistence_across_reload(self):
        memory = SimpleFileMemory(self.path)
        entry = memory.add(MemoryEntry(kind="fact", content="user likes tea"))
        reloaded = SimpleFileMemory(self.path)
        self.assertIsNotNone(reloaded.get(entry.memory_id))

    def test_search_by_keyword(self):
        memory = SimpleFileMemory(self.path)
        memory.add(MemoryEntry(kind="fact", content="user likes coffee"))
        memory.add(MemoryEntry(kind="fact", content="user likes green tea"))
        results = memory.search("coffee")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].content, "user likes coffee")

    def test_search_by_tag(self):
        memory = SimpleFileMemory(self.path)
        memory.add(MemoryEntry(kind="fact", content="remember this", metadata={"tags": ["project-x"]}))
        self.assertEqual(len(memory.search("project-x")), 1)

    def test_list_recent_orders_by_time(self):
        from datetime import datetime, timedelta, timezone

        memory = SimpleFileMemory(self.path)
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        first = memory.add(MemoryEntry(kind="note", content="first", timestamp=base))
        second = memory.add(
            MemoryEntry(kind="note", content="second", timestamp=base + timedelta(seconds=1))
        )
        recent = memory.list_recent()
        self.assertEqual(recent[0].memory_id, second.memory_id)
        self.assertEqual(recent[1].memory_id, first.memory_id)

    def test_emits_memory_created_event(self):
        bus = EventBus()
        seen = []
        bus.subscribe(lambda e: seen.append(e), EventType.MEMORY_CREATED)
        memory = SimpleFileMemory(self.path, event_bus=bus)
        entry = memory.add(MemoryEntry(kind="fact", content="x"))
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].payload["memory_id"], entry.memory_id)


if __name__ == "__main__":
    unittest.main()
