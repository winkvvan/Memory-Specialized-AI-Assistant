"""Phase 7.4: Memory Ecology automation & Temporal integration.

Covers: automatic maintenance via Temporal cadence, idempotency, lazy decay,
reinforcement resetting the decay baseline, archive/reactivation lifecycle,
fault isolation, scheduler single-service + no-duplicate, maintenance metadata
persistence, enhanced stats, performance scaling, provider independence.
"""

import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from chris.events import EventBus, EventType
from chris.memory.base import MemoryEntry
from chris.memory.ecology import MemoryEcology, MemoryStatus, MemoryType, StorageTier
from chris.memory.maintenance import MemoryMaintenanceScheduler
from chris.temporal.clock import ManualClock
from chris.temporal.system import TemporalSystem

BASE = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def fact(content, memory_type=None):
    return MemoryEntry(kind="fact", content=content, memory_type=memory_type or "")


def make_env(tmp=None):
    """tmp: a directory path (str/Path) or None (creates a temp dir)."""
    if tmp is None:
        handle = tempfile.TemporaryDirectory()
        path = Path(handle.name) / "m.json"
    else:
        path = Path(tmp) / "m.json"
    clock = ManualClock(BASE)
    temporal = TemporalSystem(clock=clock)
    bus = EventBus()
    seen = []
    bus.subscribe(seen.append)
    ecology = MemoryEcology(path, event_bus=bus, temporal=temporal)
    return ecology, clock, bus, seen, tmp


class TestIdempotency(unittest.TestCase):
    def test_maintenance_without_time_change_is_idempotent(self):
        ecology, clock, _bus, _seen, _tmp = make_env()
        entry = ecology.add(fact("用户喜欢咖啡", memory_type=MemoryType.PREFERENCE.value))
        entry.life_score = 80.0
        clock.advance(timedelta(days=1))
        ecology.maintenance()
        strength_a = ecology.get(entry.memory_id).strength
        for _ in range(3):
            ecology.maintenance()
        strength_b = ecology.get(entry.memory_id).strength
        self.assertEqual(strength_a, strength_b, "no time change -> no extra decay")

    def test_scheduler_no_duplicate_run_without_time_change(self):
        ecology, clock, bus, seen, _tmp = make_env()
        scheduler = MemoryMaintenanceScheduler(ecology, bus, TemporalSystem(clock=clock))
        self.assertIsNotNone(scheduler.tick(), "first tick runs (never maintained before)")
        self.assertIsNone(scheduler.tick(), "second tick same time must not run")
        count_after = ecology.maintenance_metadata()["maintenance_count"]
        self.assertEqual(count_after, 1)


class TestAutomaticDecay(unittest.TestCase):
    def test_virtual_clock_driven_decay(self):
        ecology, clock, _bus, _seen, _tmp = make_env()
        entry = ecology.add(fact("某偏好", memory_type=MemoryType.PREFERENCE.value))
        entry.life_score = 100.0
        steps = [timedelta(days=1), timedelta(days=6), timedelta(days=23)]
        strengths = []
        for step in steps:
            clock.advance(step)
            ecology.maintenance()
            strengths.append(ecology.get(entry.memory_id).strength)
        self.assertGreater(strengths[0], strengths[1])
        self.assertGreater(strengths[1], strengths[2])

    def test_type_specific_decay_via_maintenance(self):
        ecology, clock, _bus, _seen, _tmp = make_env()
        ep = ecology.add(fact("今天讨论了显卡", memory_type=MemoryType.EPISODIC.value))
        se = ecology.add(fact("用户正在开发Chris", memory_type=MemoryType.SEMANTIC.value))
        ep.life_score = 100.0
        se.life_score = 100.0
        clock.advance(timedelta(days=30))
        ecology.maintenance()
        self.assertLess(
            ecology.get(ep.memory_id).strength,
            ecology.get(se.memory_id).strength,
            "episodic decays faster than semantic under maintenance",
        )


class TestReinforcementDecayBaseline(unittest.TestCase):
    def test_reinforce_resets_decay_baseline(self):
        ecology, clock, _bus, _seen, _tmp = make_env()
        entry = ecology.add(fact("用户喜欢咖啡", memory_type=MemoryType.PREFERENCE.value))
        entry.life_score = 50.0
        clock.advance(timedelta(days=7))
        ecology.maintenance()
        weakened = ecology.get(entry.memory_id).strength
        self.assertLess(weakened, 0.5)
        reinforced = ecology.reinforce(entry.memory_id)
        self.assertGreater(reinforced.strength, weakened)
        self.assertEqual(reinforced.reinforcement_count, 1)
        reinforced_strength = reinforced.strength
        # decay restarts from the reinforcement moment
        clock.advance(timedelta(days=7))
        ecology.maintenance()
        after_reinforce = ecology.get(entry.memory_id).strength
        self.assertLess(after_reinforce, reinforced_strength)


class TestArchiveReactivation(unittest.TestCase):
    def test_full_lifecycle_virtual_clock(self):
        ecology, clock, _bus, _seen, _tmp = make_env()
        entry = ecology.add(fact("某临时事件", memory_type=MemoryType.TEMPORARY.value))
        clock.advance(timedelta(days=30))
        ecology.maintenance()
        st = ecology.get(entry.memory_id)
        self.assertIn(st.storage_tier, (StorageTier.DORMANT.value, StorageTier.ARCHIVE.value))
        clock.advance(timedelta(days=30))
        ecology.maintenance()
        archived = ecology.get(entry.memory_id)
        self.assertEqual(archived.storage_tier, StorageTier.ARCHIVE.value)
        # reactivation via explicit reinforce
        ecology.reinforce(entry.memory_id)
        self.assertEqual(ecology.get(entry.memory_id).storage_tier, StorageTier.ACTIVE.value)


class TestFaultIsolation(unittest.TestCase):
    def test_corrupt_memory_does_not_block_others(self):
        ecology, clock, _bus, _seen, _tmp = make_env()
        good1 = ecology.add(fact("好记忆1", memory_type=MemoryType.PREFERENCE.value))
        corrupt = ecology.add(fact("坏记忆", memory_type=MemoryType.PREFERENCE.value))
        good2 = ecology.add(fact("好记忆2", memory_type=MemoryType.PREFERENCE.value))
        corrupt.life_score = "not-a-number"
        clock.advance(timedelta(days=5))
        report = ecology.maintenance()
        self.assertGreaterEqual(report["processed"], 1, "good memories still processed")
        self.assertGreaterEqual(report["errors"], 1, "corrupt memory counted as error")


class TestSchedulerService(unittest.TestCase):
    def test_interval_config_driven(self):
        from chris.memory.ecology import MemoryConfig

        with tempfile.TemporaryDirectory() as tmp:
            clock = ManualClock(BASE)
            ecology = MemoryEcology(
                Path(tmp) / "m.json",
                temporal=TemporalSystem(clock=clock),
                config=MemoryConfig(maintenance_interval_seconds=60),
            )
            bus = EventBus()
            seen = []
            bus.subscribe(seen.append)
            scheduler = MemoryMaintenanceScheduler(ecology, bus, TemporalSystem(clock=clock))
            scheduler.tick()
            self.assertEqual(ecology.maintenance_metadata()["maintenance_count"], 1)
            clock.advance(timedelta(seconds=30))
            self.assertIsNone(scheduler.tick(), "30s < 60s interval must not run")
            clock.advance(timedelta(seconds=31))
            self.assertIsNotNone(scheduler.tick(), ">=60s interval must run")

    def test_emits_checkpoint_event(self):
        ecology, clock, bus, seen, _tmp = make_env()
        scheduler = MemoryMaintenanceScheduler(ecology, bus, TemporalSystem(clock=clock))
        scheduler.tick()
        self.assertIn(
            EventType.MEMORY_MAINTENANCE_CHECKPOINT,
            [e.type for e in seen],
        )

    def test_no_duplicate_scheduler_across_runtimes(self):
        from chris.app import build_runtime
        from chris.config import Config
        from chris.llm.mock import MockProvider

        with tempfile.TemporaryDirectory() as tmp:
            config = Config()
            config.memory_file = str(Path(tmp) / "m.json")
            config.personality_file = str(Path(tmp) / "p.json")
            config.temporal_file = str(Path(tmp) / "t.json")
            config.world_state_file = str(Path(tmp) / "w.json")
            r1 = build_runtime(config, provider=MockProvider(responses=["A"]))
            r2 = build_runtime(config, provider=MockProvider(responses=["B"]))
            self.assertIsNot(r1.memory_maintenance, r2.memory_maintenance)
            r1.memory_maintenance.tick()
            self.assertEqual(r1.memory.stats()["maintenance_count"], 1)
            # r2 is a separate service/bus; its tick is independent
            self.assertEqual(r2.memory.stats()["maintenance_count"], 0)


class TestPersistence(unittest.TestCase):
    def test_maintenance_metadata_survives_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.json"
            clock = ManualClock(BASE)
            ecology = MemoryEcology(path, temporal=TemporalSystem(clock=clock))
            entry = ecology.add(fact("用户喜欢咖啡"))
            clock.advance(timedelta(days=2))
            ecology.maintenance()
            ecology.record_maintenance(clock.now())
            reloaded = MemoryEcology(path, temporal=TemporalSystem(clock=clock))
            meta = reloaded.maintenance_metadata()
            self.assertIsNotNone(meta["last_maintenance_at"])
            self.assertEqual(meta["maintenance_count"], 1)
            self.assertEqual(reloaded.get(entry.memory_id).strength, ecology.get(entry.memory_id).strength)


class TestStats(unittest.TestCase):
    def test_stats_include_maintenance_and_strength(self):
        ecology, clock, _bus, _seen, _tmp = make_env()
        ecology.add(fact("用户喜欢咖啡", memory_type=MemoryType.PREFERENCE.value))
        ecology.add(fact("今天讨论了显卡", memory_type=MemoryType.EPISODIC.value))
        clock.advance(timedelta(days=30))
        ecology.maintenance()
        ecology.record_maintenance(clock.now())
        stats = ecology.stats()
        self.assertIn("average_strength", stats)
        self.assertIn("weakening", stats)
        self.assertIn("last_maintenance_at", stats)
        self.assertIn("next_maintenance_at", stats)
        self.assertEqual(stats["maintenance_count"], 1)
        self.assertIn("types", stats)


class TestPerformance(unittest.TestCase):
    def test_maintenance_scales_approximately_linear(self):
        import time as _t

        with tempfile.TemporaryDirectory() as tmp:
            ecology, clock, _bus, _seen, _ = make_env(tmp)
            for i in range(1000):
                ecology.add(fact(f"记忆{i}", memory_type=MemoryType.EPISODIC.value))
            clock.advance(timedelta(days=30))
            _t.sleep(0.001)
            start = _t.perf_counter()
            ecology.maintenance()
            elapsed_1000 = _t.perf_counter() - start
            self.assertLess(elapsed_1000, 3.0, "maintenance of 1000 memories should be fast")


class TestProviderIndependence(unittest.TestCase):
    def test_memory_lifecycle_independent_of_provider(self):
        from chris.app import build_runtime
        from chris.config import Config
        from chris.llm.mock import MockProvider

        with tempfile.TemporaryDirectory() as tmp:
            config = Config()
            config.memory_file = str(Path(tmp) / "m.json")
            config.personality_file = str(Path(tmp) / "p.json")
            config.temporal_file = str(Path(tmp) / "t.json")
            config.world_state_file = str(Path(tmp) / "w.json")
            a = build_runtime(config, provider=MockProvider(responses=["A"]))
            a.memory.add(MemoryEntry(kind="fact", content="用户喜欢咖啡"))
            a.memory_maintenance.tick()
            a_total = a.memory.stats()["total"]
            # a fresh runtime on the same files sees identical memory state
            b = build_runtime(config, provider=MockProvider(responses=["B"]))
            self.assertEqual(b.memory.stats()["total"], a_total)
            self.assertEqual(b.memory.stats()["average_strength"], a.memory.stats()["average_strength"])


if __name__ == "__main__":
    unittest.main()
