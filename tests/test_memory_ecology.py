"""Memory Ecology 2.0 tests: lifecycle, dormancy, recovery, conflict,
compression, archive, GC, temporal drive, persistence, provider independence."""

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from chris.events import EventBus, EventType
from chris.memory.base import MemoryEntry
from chris.memory.ecology import (
    MemoryConfig,
    MemoryEcology,
    MemorySource,
    MemoryStatus,
    MemoryType,
    StorageTier,
)
from chris.temporal.clock import ManualClock
from chris.temporal.system import TemporalSystem

BASE = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


class EcologyTestBase(unittest.TestCase):
    def make_env(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        clock = ManualClock(BASE)
        temporal = TemporalSystem(clock=clock)
        bus = EventBus()
        seen = []
        bus.subscribe(seen.append)
        ecology = MemoryEcology(
            Path(tmp.name) / "m.json", event_bus=bus, temporal=temporal
        )
        return ecology, clock, seen, tmp


def fact(content, kind="fact", memory_type=None, source=None, importance=0.5, metadata=None):
    return MemoryEntry(
        kind=kind,
        content=content,
        memory_type=memory_type or "",
        source=source or "",
        importance=importance,
        metadata=metadata or {},
    )


class TestCreation(EcologyTestBase):
    def test_add_initializes_metadata(self):
        ecology, _clock, _seen, tmp = self.make_env()
        entry = ecology.add(fact("用户喜欢咖啡", memory_type=MemoryType.PREFERENCE.value))
        self.assertEqual(entry.status, MemoryStatus.ACTIVE.value)
        self.assertEqual(entry.storage_tier, StorageTier.ACTIVE.value)
        self.assertEqual(entry.life_score, 50.0)
        self.assertEqual(entry.importance, 0.7)
        self.assertGreater(entry.confidence, 0.5)
        self.assertIsNotNone(entry.last_confirmed_at)
        self.assertIsNotNone(entry.last_used_at)

    def test_remember_sets_highest_priority(self):
        ecology, _clock, _seen, tmp = self.make_env()
        entry = ecology.add(fact("用户生日是5月18日", metadata={"remember": True}))
        self.assertEqual(entry.importance, 0.99)
        self.assertEqual(entry.source, MemorySource.EXPLICIT_USER.value)
        self.assertEqual(entry.life_score, 70.0)
        self.assertEqual(entry.confidence, 0.98)
        self.assertEqual(entry.status, MemoryStatus.ACTIVE.value)

    def test_list_remembered(self):
        ecology, _clock, _seen, tmp = self.make_env()
        ecology.add(fact("普通记忆"))
        remembered = ecology.add(fact("我对象叫小明", metadata={"remember": True}))
        ecology.add(fact("我也喜欢可乐", metadata={"remember": True}))
        ids = [e.memory_id for e in ecology.list_remembered()]
        self.assertIn(remembered.memory_id, ids)
        self.assertEqual(len(ids), 2)
        ecology.archive_one(remembered.memory_id)
        ids_after = [e.memory_id for e in ecology.list_remembered()]
        self.assertNotIn(remembered.memory_id, ids_after, "archived remember should not be listed as current")

    def test_remember_decays_much_slower_than_normal(self):
        ecology, clock, _seen, tmp = self.make_env()
        remember = ecology.add(fact("我对象叫小明", metadata={"remember": True}))
        normal = ecology.add(fact("用户普通偏好", memory_type=MemoryType.PREFERENCE.value))
        clock.advance(timedelta(days=30))
        ecology.process_due()
        remember_life = ecology.get(remember.memory_id).life_score
        normal_life = ecology.get(normal.memory_id).life_score
        self.assertGreater(remember_life, normal_life, "remember must decay slower than normal")
        self.assertEqual(ecology.get(remember.memory_id).storage_tier, StorageTier.ACTIVE.value)

    def test_remember_still_active_after_5_years_but_not_eternal(self):
        ecology, clock, _seen, tmp = self.make_env()
        mem = ecology.add(fact("我对象叫小明", metadata={"remember": True}))
        clock.advance(timedelta(days=365 * 5))
        ecology.process_due()
        st = ecology.get(mem.memory_id)
        self.assertEqual(st.storage_tier, StorageTier.ACTIVE.value, "remember still active at 5 years")
        self.assertLess(st.life_score, 70.0, "life should have decayed, not held at floor")
        clock.advance(timedelta(days=365 * 10))
        ecology.process_due()
        st = ecology.get(mem.memory_id)
        self.assertNotEqual(
            st.storage_tier, StorageTier.ACTIVE.value,
            "remember eventually leaves the active tier (not eternal)",
        )
        tmp.cleanup()

    def test_source_sets_confidence(self):
        ecology, _clock, _seen, tmp = self.make_env()
        explicit = ecology.add(fact("a", source=MemorySource.EXPLICIT_USER.value))
        inferred = ecology.add(fact("b", source=MemorySource.LLM_INFERENCE.value))
        self.assertGreater(explicit.confidence, inferred.confidence)
        tmp.cleanup()


class TestLifeScore(EcologyTestBase):
    def test_reinforce_raises_and_caps(self):
        ecology, _clock, _seen, tmp = self.make_env()
        entry = ecology.add(fact("用户喜欢咖啡"))
        initial = entry.life_score
        ecology.reinforce(entry.memory_id)
        self.assertGreater(ecology.get(entry.memory_id).life_score, initial)
        for _ in range(30):
            ecology.reinforce(entry.memory_id)
        self.assertLessEqual(ecology.get(entry.memory_id).life_score, 90.0)

    def test_decay_lowers_life_with_virtual_clock(self):
        ecology, clock, _seen, tmp = self.make_env()
        entry = ecology.add(fact("临时状态", memory_type=MemoryType.TEMPORARY_STATE.value))
        initial = entry.life_score
        clock.advance(timedelta(days=30))
        ecology.decay(entry.memory_id)
        self.assertLess(ecology.get(entry.memory_id).life_score, initial)

    def test_life_and_confidence_independent(self):
        ecology, _clock, _seen, tmp = self.make_env()
        entry = ecology.add(fact("很久没用的高置信记忆"))
        entry.life_score = 5.0
        entry.confidence = 0.98
        self.assertEqual(entry.life_score, 5.0)
        self.assertEqual(entry.confidence, 0.98)
        tmp.cleanup()


class TestDormancyAndRecovery(EcologyTestBase):
    def test_decay_to_dormant_and_prompt_excludes_it(self):
        ecology, clock, _seen, tmp = self.make_env()
        ecology._config.archive_after_days = 100000  # keep it dormant, not archived
        entry = ecology.add(fact("2026年喜欢的游戏", memory_type=MemoryType.PREFERENCE.value))
        clock.advance(timedelta(days=380))  # life ~12 -> DORMANT band (10..15)
        ecology.decay(entry.memory_id)
        stored = ecology.get(entry.memory_id)
        self.assertEqual(stored.status, MemoryStatus.DORMANT.value)
        self.assertEqual(stored.storage_tier, StorageTier.DORMANT.value)
        self.assertNotIn(entry.memory_id, [e.memory_id for e in ecology.list_recent()])

    def test_reinforce_recovers_dormant_to_active(self):
        ecology, clock, _seen, tmp = self.make_env()
        entry = ecology.add(fact("用户喜欢咖啡", memory_type=MemoryType.PREFERENCE.value))
        clock.advance(timedelta(days=3650))
        ecology.decay(entry.memory_id)
        ecology.reinforce(entry.memory_id)
        recovered = ecology.get(entry.memory_id)
        self.assertEqual(recovered.status, MemoryStatus.RECOVERED.value)
        self.assertEqual(recovered.storage_tier, StorageTier.ACTIVE.value)
        self.assertIn(entry.memory_id, [e.memory_id for e in ecology.list_recent()])
        tmp.cleanup()

    def test_archive_recovery_no_duplicate(self):
        ecology, clock, _seen, tmp = self.make_env()
        entry = ecology.add(fact("2026年用户喜欢某款游戏", memory_type=MemoryType.PREFERENCE.value))
        ecology.archive_one(entry.memory_id)
        self.assertEqual(ecology.get(entry.memory_id).storage_tier, StorageTier.ARCHIVE.value)
        ecology.reinforce(entry.memory_id)
        self.assertEqual(ecology.get(entry.memory_id).storage_tier, StorageTier.ACTIVE.value)
        self.assertEqual(len(ecology.list_active()), 1)
        tmp.cleanup()


class TestContradiction(EcologyTestBase):
    def test_supersede_keeps_history(self):
        ecology, _clock, seen, tmp = self.make_env()
        old = ecology.add(fact("用户喜欢咖啡"))
        new = ecology.supersede(old.memory_id, fact("用户现在不喝咖啡了"))
        old_after = ecology.get(old.memory_id)
        self.assertEqual(old_after.status, MemoryStatus.SUPERSEDED.value)
        self.assertEqual(old_after.superseded_by, new.memory_id)
        self.assertEqual(new.supersedes, old.memory_id)
        self.assertEqual(new.status, MemoryStatus.ACTIVE.value)
        event_types = [e.type for e in seen]
        self.assertIn(EventType.MEMORY_SUPERSEDED, event_types)
        self.assertIn(EventType.MEMORY_CONTRADICTED, event_types)
        tmp.cleanup()

    def test_invalidate(self):
        ecology, _clock, seen, tmp = self.make_env()
        entry = ecology.add(fact("错误信息"))
        ecology.invalidate(entry.memory_id)
        self.assertEqual(ecology.get(entry.memory_id).status, MemoryStatus.INVALIDATED.value)
        self.assertIn(EventType.MEMORY_INVALIDATED, [e.type for e in seen])
        tmp.cleanup()


class TestArchiveAndCompression(EcologyTestBase):
    def test_archive_moves_out_of_active(self):
        ecology, _clock, _seen, tmp = self.make_env()
        entry = ecology.add(fact("临时事件", memory_type=MemoryType.EVENT.value))
        ecology.archive_one(entry.memory_id)
        self.assertEqual(ecology.get(entry.memory_id).storage_tier, StorageTier.ARCHIVE.value)
        self.assertNotIn(entry.memory_id, [e.memory_id for e in ecology.list_recent()])
        tmp.cleanup()

    def test_compress_preserves_mapping(self):
        ecology, _clock, _seen, tmp = self.make_env()
        ids = []
        for i in range(5):
            entry = ecology.add(fact(f"用户玩游戏A 第{i}次", metadata={"topic": "gameA"}))
            ecology.archive_one(entry.memory_id)
            ids.append(entry.memory_id)
        created = ecology.compress(topic="gameA")
        self.assertTrue(created)
        compressed = created[0]
        self.assertEqual(set(compressed.compressed_from), set(ids))
        self.assertEqual(compressed.storage_tier, StorageTier.ARCHIVE.value)
        for mid in ids:
            self.assertEqual(ecology.get(mid).metadata.get("compressed_into"), compressed.memory_id)
        tmp.cleanup()


class TestGarbageCollection(EcologyTestBase):
    def test_low_life_high_importance_survives_gc(self):
        ecology, clock, _seen, tmp = self.make_env()
        birthday = ecology.add(fact("用户生日是5月18日", importance=0.99))
        birthday.life_score = 1.0
        birthday.confidence = 0.99
        clock.advance(timedelta(days=3650))
        removed = ecology.gc()
        self.assertNotIn(birthday.memory_id, [e.memory_id for e in removed])
        self.assertIsNotNone(ecology.get(birthday.memory_id))
        tmp.cleanup()

    def test_low_value_old_gc_and_high_value_kept(self):
        ecology, clock, seen, tmp = self.make_env()
        junk = ecology.add(fact("某天喝了什么饮料", memory_type=MemoryType.EVENT.value))
        junk.importance = 0.05
        birthday = ecology.add(fact("用户生日是5月18日", importance=0.99))
        clock.advance(timedelta(days=4000))
        ecology.process_due()  # let life decay so GC can consider junk
        removed = ecology.gc()
        self.assertIn(junk.memory_id, [e.memory_id for e in removed])
        self.assertIsNotNone(ecology.get(birthday.memory_id))
        self.assertIn(EventType.MEMORY_GARBAGE_COLLECTED, [e.type for e in seen])

    def test_gc_disabled(self):
        ecology, clock, _seen, tmp = self.make_env()
        ecology._config.gc_enabled = False
        entry = ecology.add(fact("低价值", memory_type=MemoryType.TEMPORARY_STATE.value))
        entry.importance = 0.01
        entry.life_score = 1.0
        clock.advance(timedelta(days=4000))
        self.assertEqual(ecology.gc(), [])
        self.assertIsNotNone(ecology.get(entry.memory_id))
        tmp.cleanup()


class TestTemporalIntegration(EcologyTestBase):
    def test_virtual_clock_drives_lifecycle(self):
        ecology, clock, _seen, tmp = self.make_env()
        ecology._config.archive_after_days = 100000  # stay dormant for phase 1
        entry = ecology.add(fact("用户喜欢咖啡", memory_type=MemoryType.PREFERENCE.value))
        clock.advance(timedelta(days=400))
        ecology.process_due()
        self.assertEqual(ecology.get(entry.memory_id).status, MemoryStatus.DORMANT.value)
        ecology._config.archive_after_days = 0
        clock.advance(timedelta(days=300))
        ecology.process_due()
        self.assertEqual(ecology.get(entry.memory_id).storage_tier, StorageTier.ARCHIVE.value)
        tmp.cleanup()


class TestPersistence(unittest.TestCase):
    def test_restart_preserves_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.json"
            clock = ManualClock(BASE)
            ecology = MemoryEcology(path, temporal=TemporalSystem(clock=clock))
            entry = ecology.add(fact("用户喜欢咖啡"))
            ecology.archive_one(entry.memory_id)
            reloaded = MemoryEcology(path, temporal=TemporalSystem(clock=clock))
            self.assertEqual(reloaded.get(entry.memory_id).storage_tier, StorageTier.ARCHIVE.value)
            self.assertEqual(reloaded.get(entry.memory_id).status, MemoryStatus.ARCHIVED.value)
            self.assertEqual(len(reloaded.list_archived()), 1)


class TestStats(EcologyTestBase):
    def test_stats_counts_tiers(self):
        ecology, clock, _seen, tmp = self.make_env()
        ecology.add(fact("活跃记忆"))
        dormant = ecology.add(fact("休眠记忆"))
        dormant.life_score = 1.0
        ecology._entries[dormant.memory_id].storage_tier = StorageTier.DORMANT.value
        archived = ecology.add(fact("归档记忆"))
        ecology.archive_one(archived.memory_id)
        stats = ecology.stats()
        self.assertEqual(stats["active"], 1)
        self.assertEqual(stats["dormant"], 1)
        self.assertEqual(stats["archived"], 1)
        self.assertEqual(stats["total"], 3)
        tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
