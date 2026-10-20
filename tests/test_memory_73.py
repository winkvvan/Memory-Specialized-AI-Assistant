"""Phase 7.3: Memory Ecology lifecycle tests.

Covers: strength [0,1], lazy decay on access/retrieval, decay policy (type-aware,
config-driven), reinforcement vs access separation, archive/reactivation,
ranking (relevant+important over merely-recent), duplicate merge, virtual time
(+1h/+1d/+7d/+30d), restart persistence, proposal layer, maintenance,
PatternCandidate interface.
"""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from chris.memory.base import MemoryEntry
from chris.memory.ecology import (
    MemoryConfig,
    MemoryDecayPolicy,
    MemoryEcology,
    MemoryOperation,
    MemoryOperationProposal,
    MemoryStatus,
    MemoryType,
    PatternCandidate,
    StorageTier,
)
from chris.temporal.clock import ManualClock
from chris.temporal.system import TemporalSystem

BASE = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def make_env(tmp=None, config=None):
    """tmp: a directory path (str/Path) or None (creates a temp dir)."""
    if tmp is None:
        handle = tempfile.TemporaryDirectory()
        path = Path(handle.name) / "m.json"
    else:
        path = Path(tmp) / "m.json"
    clock = ManualClock(BASE)
    temporal = TemporalSystem(clock=clock)
    ecology = MemoryEcology(path, temporal=temporal, config=config)
    return ecology, clock, tmp


def fact(content, memory_type=None, metadata=None):
    return MemoryEntry(
        kind="fact", content=content, memory_type=memory_type or "", metadata=metadata or {}
    )


class TestStrengthAndDecay(unittest.TestCase):
    def test_strength_in_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            ecology, _c, _t = make_env(tmp)
            entry = ecology.add(fact("用户喜欢咖啡"))
            self.assertGreaterEqual(entry.strength, 0.0)
            self.assertLessEqual(entry.strength, 1.0)
            entry.life_score = 150.0
            self.assertEqual(entry.strength, 1.0)
            entry.life_score = -5.0
            self.assertEqual(entry.strength, 0.0)

    def test_lazy_decay_applied_on_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            ecology, clock, _t = make_env(tmp)
            entry = ecology.add(fact("用户喜欢咖啡", memory_type=MemoryType.PREFERENCE.value))
            entry.life_score = 100.0
            clock.advance(timedelta(days=30))
            before = entry.strength
            accessed = ecology.access(entry.memory_id)  # no process_due needed
            self.assertLess(accessed.strength, before, "lazy decay must run on access")

    def test_virtual_time_monotonic_decay(self):
        with tempfile.TemporaryDirectory() as tmp:
            ecology, clock, _t = make_env(tmp)
            entry = ecology.add(fact("某偏好", memory_type=MemoryType.PREFERENCE.value))
            entry.life_score = 100.0
            strengths = [entry.strength]
            steps = [timedelta(hours=1), timedelta(days=1), timedelta(days=7), timedelta(days=30)]
            for step in steps:
                clock.advance(step)
                ecology.access(entry.memory_id)
                strengths.append(ecology.get(entry.memory_id).strength)
            for i in range(1, len(strengths)):
                self.assertLess(strengths[i], strengths[i - 1], f"strength must decay at step {i}")


class TestReinforcement(unittest.TestCase):
    def test_reinforce_boosts_and_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            ecology, _clock, _t = make_env(tmp)
            entry = ecology.add(fact("用户喜欢咖啡"))
            before = entry.strength
            reinforced = ecology.reinforce(entry.memory_id)
            self.assertGreaterEqual(reinforced.strength, before)
            self.assertEqual(reinforced.reinforcement_count, 1)
            self.assertIsNotNone(reinforced.last_confirmed_at)

    def test_reinforcement_capped(self):
        with tempfile.TemporaryDirectory() as tmp:
            ecology, _clock, _t = make_env(tmp)
            entry = ecology.add(fact("用户喜欢咖啡"))
            for _ in range(200):
                ecology.reinforce(entry.memory_id)
            self.assertLessEqual(entry.strength, 1.0)

    def test_access_does_not_boost(self):
        with tempfile.TemporaryDirectory() as tmp:
            ecology, _clock, _t = make_env(tmp)
            entry = ecology.add(fact("用户喜欢咖啡"))
            entry.life_score = 50.0
            ecology.access(entry.memory_id)
            self.assertEqual(ecology.get(entry.memory_id).strength, 0.5)


class TestTypeSpecificDecay(unittest.TestCase):
    def test_episodic_decays_faster_than_semantic(self):
        with tempfile.TemporaryDirectory() as tmp:
            ecology, clock, _t = make_env(tmp)
            episodic = ecology.add(fact("今天讨论了显卡", memory_type=MemoryType.EPISODIC.value))
            semantic = ecology.add(fact("用户正在开发Chris", memory_type=MemoryType.SEMANTIC.value))
            episodic.life_score = 100.0
            semantic.life_score = 100.0
            clock.advance(timedelta(days=30))
            ecology.process_due()
            self.assertLess(
                ecology.get(episodic.memory_id).strength,
                ecology.get(semantic.memory_id).strength,
                "episodic decays faster than semantic",
            )


class TestArchiveReactivation(unittest.TestCase):
    def test_archive_and_reactivate_via_reinforce(self):
        with tempfile.TemporaryDirectory() as tmp:
            ecology, clock, _t = make_env(tmp)
            entry = ecology.add(fact("某临时事件", memory_type=MemoryType.TEMPORARY.value))
            clock.advance(timedelta(days=60))
            ecology.process_due()
            st = ecology.get(entry.memory_id)
            self.assertIn(st.storage_tier, (StorageTier.DORMANT.value, StorageTier.ARCHIVE.value))
            # explicit re-mention (reinforce) reactivates
            reinforced = ecology.reinforce(entry.memory_id)
            self.assertEqual(reinforced.storage_tier, StorageTier.ACTIVE.value)
            self.assertNotIn(entry.memory_id, [e.memory_id for e in ecology.list_archived()])


class TestRanking(unittest.TestCase):
    def test_relevant_important_ranks_above_merely_recent(self):
        with tempfile.TemporaryDirectory() as tmp:
            ecology, clock, _t = make_env(tmp)
            important = ecology.add(
                fact("Chris 项目正在开发", memory_type=MemoryType.PROJECT.value)
            )
            important.importance = 0.95
            recent_junk = ecology.add(fact("我今天中午吃了面", memory_type=MemoryType.EPISODIC.value))
            clock.advance(timedelta(days=2))
            results = ecology.query(text="Chris")
            self.assertTrue(results)
            self.assertIn("Chris", results[0].memory.content)
            self.assertGreater(results[0].score, 0)


class TestDuplicate(unittest.TestCase):
    def test_duplicate_merges(self):
        with tempfile.TemporaryDirectory() as tmp:
            ecology, _clock, _t = make_env(tmp)
            first = ecology.add(fact("我喜欢咖啡"))
            second = ecology.add(fact("我喜欢喝咖啡"))
            self.assertEqual(first.memory_id, second.memory_id)
            self.assertEqual(len(ecology.list_all()), 1)


class TestDecayPolicy(unittest.TestCase):
    def test_config_driven_half_life(self):
        config = MemoryConfig(decay={"PREFERENCE": 30.0, "EPISODIC": 3.0})
        policy = MemoryDecayPolicy.from_config(config)
        self.assertEqual(policy.half_life("PREFERENCE"), 30.0)
        self.assertEqual(policy.half_life("EPISODIC"), 3.0)
        entry = MemoryEntry(content="x", memory_type=MemoryType.PREFERENCE.value, life_score=100.0)
        entry.last_decay_at = BASE
        later = BASE + timedelta(days=30)
        self.assertAlmostEqual(
            policy.effective_life(entry, later), 50.0, places=6, msg="30d at 30d half-life"
        )


class TestProposalLayer(unittest.TestCase):
    def test_llm_proposes_create_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            ecology, _clock, _t = make_env(tmp)
            stored = ecology.propose(
                MemoryOperationProposal(
                    operation=MemoryOperation.CREATE.value,
                    content="用户喜欢冰美式",
                    remember=True,
                    source="LLM_INFERENCE",
                )
            )
            self.assertIsNotNone(stored)
            self.assertEqual(stored.status, MemoryStatus.ACTIVE.value)
            self.assertIn(stored.memory_id, [e.memory_id for e in ecology.list_remembered()])

    def test_ignored_content_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            ecology, _clock, _t = make_env(tmp)
            stored = ecology.propose(
                MemoryOperationProposal(
                    operation=MemoryOperation.CREATE.value, content="今天天气不错", source="LLM_INFERENCE"
                )
            )
            self.assertIsNone(stored, "low-value proposal must be rejected by policy")

    def test_reinforce_and_reactivate_via_proposal(self):
        with tempfile.TemporaryDirectory() as tmp:
            ecology, _clock, _t = make_env(tmp)
            entry = ecology.add(fact("用户喜欢咖啡"))
            ecology.archive_one(entry.memory_id)
            reactivated = ecology.propose(
                MemoryOperationProposal(
                    operation=MemoryOperation.REACTIVATE.value, memory_id=entry.memory_id
                )
            )
            self.assertEqual(reactivated.storage_tier, StorageTier.ACTIVE.value)
            ecology.propose(
                MemoryOperationProposal(
                    operation=MemoryOperation.REINFORCE.value, memory_id=entry.memory_id
                )
            )
            self.assertGreaterEqual(ecology.get(entry.memory_id).reinforcement_count, 1)


class TestMaintenance(unittest.TestCase):
    def test_maintenance_archives_low_strength(self):
        with tempfile.TemporaryDirectory() as tmp:
            ecology, clock, _t = make_env(tmp)
            for i in range(10):
                ecology.add(fact(f"临时事件{i}", memory_type=MemoryType.TEMPORARY.value))
            clock.advance(timedelta(days=60))
            report = ecology.maintenance()
            self.assertGreater(report["decayed"], 0)
            self.assertGreaterEqual(report["interval_seconds"], 300)
            self.assertGreater(
                sum(1 for e in ecology.list_archived()), 0, "maintenance should archive low-strength"
            )


class TestPatternCandidate(unittest.TestCase):
    def test_candidate_to_proposal(self):
        candidate = PatternCandidate(pattern="用户经常晚上开发", confidence=0.6)
        proposal = candidate.to_proposal()
        self.assertEqual(proposal.operation, MemoryOperation.CREATE.value)
        self.assertEqual(proposal.memory_type, MemoryType.BEHAVIORAL.value)
        self.assertEqual(proposal.source, "world_observation")


class TestRestartPersistence(unittest.TestCase):
    def test_strength_timestamps_counts_survive_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.json"
            clock = ManualClock(BASE)
            ecology = MemoryEcology(path, temporal=TemporalSystem(clock=clock))
            entry = ecology.add(fact("用户喜欢咖啡", metadata={"remember": True}))
            ecology.reinforce(entry.memory_id)
            ecology.access(entry.memory_id)
            before = ecology.get(entry.memory_id)

            reloaded = MemoryEcology(path, temporal=TemporalSystem(clock=clock))
            after = reloaded.get(entry.memory_id)
            self.assertEqual(after.life_score, before.life_score)
            self.assertEqual(after.strength, before.strength)
            self.assertEqual(after.status, before.status)
            self.assertEqual(after.access_count, before.access_count)
            self.assertEqual(after.reinforcement_count, before.reinforcement_count)
            self.assertEqual(after.last_accessed_at, before.last_accessed_at)


if __name__ == "__main__":
    unittest.main()
