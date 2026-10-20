"""Memory 2.0 Foundation tests: schema v2, admission, vitality, access
reinforcement, forget, dedup, retrieval ranking, migration/backup, pressure."""

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from chris.events import EventBus, EventType
from chris.memory.base import MemoryEntry, MemoryQuery
from chris.memory.ecology import (
    MemoryAdmissionPolicy,
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


class EcologyBase(unittest.TestCase):
    def make_env(self, tmp=None):
        tmp = tmp or tempfile.TemporaryDirectory()
        clock = ManualClock(BASE)
        temporal = TemporalSystem(clock=clock)
        bus = EventBus()
        seen = []
        bus.subscribe(seen.append)
        path = Path(tmp.name) / "m.json"
        ecology = MemoryEcology(path, event_bus=bus, temporal=temporal)
        return ecology, clock, seen, tmp, path


def fact(content, memory_type=None, metadata=None, source=""):
    return MemoryEntry(
        kind="fact",
        content=content,
        memory_type=memory_type or "",
        source=source or "",
        metadata=metadata or {},
    )


class TestSchemaV2(unittest.TestCase):
    def test_new_fields_and_vitality(self):
        with tempfile.TemporaryDirectory() as tmp:
            ecology = MemoryEcology(Path(tmp) / "m.json")
            entry = ecology.add(fact("用户是通信工程专业", memory_type=MemoryType.SEMANTIC.value))
            entry.life_score = 85.0
            entry.tags = ["专业"]
            entry.source_event_id = "evt_001"
            entry.created_by = "user"
            restored = MemoryEntry.from_dict(entry.to_dict())
            self.assertEqual(restored.tags, ["专业"])
            self.assertEqual(restored.source_event_id, "evt_001")
            self.assertEqual(restored.created_by, "user")
            self.assertAlmostEqual(entry.vitality, 0.85, places=6)

    def test_vitality_bounds(self):
        entry = MemoryEntry(content="x")
        entry.life_score = 200.0
        self.assertEqual(entry.vitality, 1.0)
        entry.life_score = -5.0
        self.assertEqual(entry.vitality, 0.0)


class TestMigrationBackup(unittest.TestCase):
    def test_migration_no_data_loss_and_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.json"
            old_entry = {
                "memory_id": "mem_v1",
                "kind": "fact",
                "content": "用户喜欢咖啡",
                "importance": 0.5,
                "timestamp": BASE.isoformat(),
                "metadata": {},
                "memory_type": "PREFERENCE",
                "source": "EXPLICIT_USER",
                "life_score": 50.0,
                "confidence": 0.9,
                "status": "ACTIVE",
                "storage_tier": "ACTIVE",
            }
            path.write_text(json.dumps([old_entry]), encoding="utf-8")  # v1 plain list
            ecology = MemoryEcology(path)
            entry = ecology.get("mem_v1")
            self.assertIsNotNone(entry, "migration must not lose data")
            self.assertEqual(entry.content, "用户喜欢咖啡")
            backups = list((Path(tmp) / "backups").glob("*.json"))
            self.assertTrue(backups, "migration must create a backup")

    def test_save_writes_schema_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.json"
            ecology = MemoryEcology(path)
            ecology.add(fact("x"))
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
            self.assertEqual(raw["schema_version"], MemoryConfig().schema_version)
            self.assertIn("entries", raw)


class TestAdmission(unittest.TestCase):
    def setUp(self):
        self.policy = MemoryAdmissionPolicy()

    def test_trivial_chat_ignored(self):
        action, _type, importance = self.policy.evaluate("我今天中午吃了面")
        self.assertIn(action, ("IGNORE", "STORE"))
        self.assertLess(importance, 0.5, "trivial info must not be high-importance")

    def test_explicit_remember_high_priority(self):
        action, mtype, importance = self.policy.evaluate(
            "记住，我以后不喜欢别人叫我老板", explicit_remember=True
        )
        self.assertEqual(action, "STORE")
        self.assertGreaterEqual(importance, 0.8)
        self.assertIn(mtype, (MemoryType.PREFERENCE.value, MemoryType.RELATIONSHIP.value))

    def test_preference_classification(self):
        _a, mtype, _i = self.policy.evaluate("我喜欢简洁的UI")
        self.assertEqual(mtype, MemoryType.PREFERENCE.value)

    def test_project_classification(self):
        _a, mtype, _i = self.policy.evaluate("Chris 项目进入 7.2 阶段")
        self.assertEqual(mtype, MemoryType.PROJECT.value)

    def test_experiment_a_low_value(self):
        action, mtype, importance = self.policy.evaluate("我今天中午吃了面")
        self.assertLess(importance, 0.5)
        if action == "STORE":
            self.assertEqual(mtype, MemoryType.EPISODIC.value)


class TestVitality(unittest.TestCase):
    def test_time_decay(self):
        ecology, clock, _seen, tmp, _p = EcologyBase().make_env()
        entry = ecology.add(fact("用户喜欢游戏A", memory_type=MemoryType.PREFERENCE.value))
        entry.life_score = 100.0
        clock.advance(timedelta(days=30))
        ecology.process_due()
        after = ecology.get(entry.memory_id)
        self.assertLess(after.vitality, 1.0, "vitality must decay with time")
        self.assertGreaterEqual(after.vitality, 0.0)

    def test_access_counts_but_does_not_boost_strength(self):
        ecology, _clock, _seen, tmp, _p = EcologyBase().make_env()
        entry = ecology.add(fact("用户喜欢游戏A"))
        entry.life_score = 50.0
        first = ecology.access(entry.memory_id)
        self.assertEqual(first.access_count, 1)
        self.assertIsNotNone(first.last_accessed_at)
        self.assertLessEqual(first.strength, 0.5, "access must not boost strength")

    def test_reinforcement_boosts_with_diminishing_returns(self):
        ecology, _clock, _seen, tmp, _p = EcologyBase().make_env()
        entry = ecology.add(fact("用户喜欢游戏A"))
        entry.life_score = 10.0
        boosts = []
        for _ in range(50):
            entry = ecology.reinforce(entry.memory_id)
            boosts.append(entry.life_score)
        self.assertLessEqual(entry.life_score, ecology._config.reinforce_cap, "strength capped")
        self.assertGreater(boosts[1] - boosts[0], boosts[-1] - boosts[-2], "diminishing returns")

    def test_experiment_d_virtual_clock_decay(self):
        ecology, clock, _seen, tmp, _p = EcologyBase().make_env()
        entry = ecology.add(fact("某临时状态", memory_type=MemoryType.TEMPORARY.value))
        entry.life_score = 100.0
        clock.advance(timedelta(days=30))
        ecology.process_due()
        self.assertLess(ecology.get(entry.memory_id).vitality, 1.0)

    def test_experiment_e_reaccess_reactivates(self):
        ecology, clock, _seen, tmp, _p = EcologyBase().make_env()
        entry = ecology.add(fact("某偏好", memory_type=MemoryType.PREFERENCE.value))
        entry.life_score = 100.0
        clock.advance(timedelta(days=60))
        ecology.process_due()
        low = ecology.get(entry.memory_id)
        before = low.vitality
        self.assertLess(before, 1.0)
        # access = retrieval: counts, no strong boost; explicit re-mention (reinforce) reactivates
        accessed = ecology.access(entry.memory_id)
        self.assertGreaterEqual(accessed.access_count, 1)
        self.assertIsNotNone(accessed.last_accessed_at)
        accessed_strength = accessed.strength
        reinforced = ecology.reinforce(entry.memory_id)
        self.assertGreater(reinforced.strength, accessed_strength, "reinforce raises strength")


class TestLifecycle(unittest.TestCase):
    def test_experiment_h_semantic_long_term(self):
        ecology, clock, _seen, tmp, _p = EcologyBase().make_env()
        semantic = ecology.add(fact("我是通信工程专业", memory_type=MemoryType.SEMANTIC.value))
        temp = ecology.add(fact("我下午要测试API", memory_type=MemoryType.TEMPORARY.value))
        clock.advance(timedelta(days=365))
        ecology.process_due()
        semantic_st = ecology.get(semantic.memory_id)
        temp_st = ecology.get(temp.memory_id)
        self.assertGreater(semantic_st.vitality, temp_st.vitality, "SEMANTIC decays slower than TEMPORARY")

    def test_archive_not_in_active(self):
        ecology, clock, _seen, tmp, _p = EcologyBase().make_env()
        entry = ecology.add(fact("某事件", memory_type=MemoryType.EPISODIC.value))
        clock.advance(timedelta(days=500))
        ecology.process_due()
        st = ecology.get(entry.memory_id)
        self.assertIn(st.storage_tier, (StorageTier.DORMANT.value, StorageTier.ARCHIVE.value))
        self.assertNotIn(entry.memory_id, [e.memory_id for e in ecology.list_recent()])


class TestForget(unittest.TestCase):
    def test_experiment_g_forget_and_restore(self):
        ecology, _clock, _seen, tmp, _p = EcologyBase().make_env()
        entry = ecology.add(fact("用户喜欢咖啡"))
        ecology.forget(entry.memory_id)
        self.assertEqual(ecology.get(entry.memory_id).status, MemoryStatus.FORGOTTEN.value)
        self.assertEqual(ecology.search("咖啡"), [], "FORGOTTEN must not be retrieved")
        self.assertEqual(ecology.query(text="咖啡"), [], "FORGOTTEN excluded from ranking")
        ecology.restore(entry.memory_id)
        self.assertNotEqual(ecology.get(entry.memory_id).status, MemoryStatus.FORGOTTEN.value)
        self.assertTrue(ecology.search("咖啡"), "restored memory is retrievable again")


class TestDedupAndConflict(unittest.TestCase):
    def test_experiment_c_similar_content_merges(self):
        ecology, _clock, _seen, tmp, _p = EcologyBase().make_env()
        first = ecology.add(fact("我喜欢咖啡"))
        second = ecology.add(fact("我喜欢喝咖啡"))
        self.assertEqual(first.memory_id, second.memory_id, "similar content must merge, not duplicate")
        self.assertEqual(len(ecology.list_all()), 1)

    def test_experiment_f_conflict_supersedes(self):
        ecology, _clock, _seen, tmp, _p = EcologyBase().make_env()
        old = ecology.add(fact("我喜欢咖啡"))
        new = ecology.supersede(old.memory_id, fact("我现在不喝咖啡了"))
        self.assertEqual(ecology.get(old.memory_id).status, MemoryStatus.SUPERSEDED.value)
        self.assertEqual(ecology.get(new.memory_id).status, MemoryStatus.ACTIVE.value)
        self.assertEqual(ecology.get(old.memory_id).superseded_by, new.memory_id)


class TestRetrievalRanking(unittest.TestCase):
    def test_query_returns_score_and_reason(self):
        ecology, _clock, _seen, tmp, _p = EcologyBase().make_env()
        ecology.add(fact("Chris 项目正在开发"))
        ecology.add(fact("用户喜欢冰美式"))
        results = ecology.query(text="Chris")
        self.assertTrue(results)
        top = results[0]
        self.assertIn("Chris", top.memory.content)
        self.assertGreater(top.score, 0)
        for key in ("relevance", "importance", "vitality", "confidence", "recency"):
            self.assertIn(key, top.reason)
        self.assertEqual(top.memory.memory_type, MemoryType.PROJECT.value)

    def test_experiment_recall_ordering(self):
        ecology, _clock, _seen, tmp, _p = EcologyBase().make_env()
        ecology.add(fact("第六阶段已完成"))
        ecology.add(fact("Chris 项目正在开发"))
        results = ecology.query(text="Chris")
        self.assertEqual(results[0].memory.content, "Chris 项目正在开发")
        self.assertGreater(results[0].score, 0.3)


class TestRepository(unittest.TestCase):
    def test_repository_contract(self):
        from chris.memory.base import MemoryRepository

        with tempfile.TemporaryDirectory() as tmp:
            ecology = MemoryEcology(Path(tmp) / "m.json")
            self.assertIsInstance(ecology, MemoryRepository)
            entry = ecology.create(fact("repository test"))
            self.assertEqual(ecology.get(entry.memory_id).content, "repository test")
            self.assertIn(entry.memory_id, [e.memory_id for e in ecology.list_all()])


class TestProviderIndependenceAndRestart(unittest.TestCase):
    def test_restart_and_provider_independence(self):
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
            a.memory.add(MemoryEntry(kind="fact", content="用户喜欢咖啡", metadata={"remember": True}))
            state_a = a.memory.stats()
            b = build_runtime(config, provider=MockProvider(responses=["B"]))
            self.assertEqual(b.memory.stats()["total"], state_a["total"])
            self.assertTrue(b.memory.search("咖啡"))


class TestPressure(unittest.TestCase):
    NOUNS = [
        "北京", "上海", "广州", "深圳", "杭州", "成都", "武汉", "西安", "南京", "重庆",
        "青岛", "长沙", "苏州", "厦门", "宁波", "无锡", "大连", "福州", "济南", "合肥",
        "昆明", "南昌", "太原", "贵阳", "兰州", "南宁", "哈尔滨", "长春", "沈阳", "石家庄",
        "郑州", "天津", "乌鲁木齐", "呼和浩特", "西宁", "银川", "拉萨", "海口", "三亚", "珠海",
        "东莞", "佛山", "中山", "惠州", "汕头", "泉州", "温州", "嘉兴", "金华", "绍兴",
        "台州", "烟台", "潍坊", "临沂", "洛阳", "徐州", "唐山", "邯郸", "保定", "沧州",
    ]

    def test_60_memories_lifecycle_and_retrieval(self):
        ecology, clock, _seen, tmp, _p = EcologyBase().make_env()
        for noun in self.NOUNS:
            mtype = [MemoryType.SEMANTIC.value, MemoryType.EPISODIC.value, MemoryType.TEMPORARY.value][
                self.NOUNS.index(noun) % 3
            ]
            ecology.add(fact(f"用户提到了关于{noun}的细节", memory_type=mtype))
        self.assertEqual(len(ecology.list_all()), 60, "distinct memories must not be deduped")
        clock.advance(timedelta(days=200))
        ecology.process_due()
        stats = ecology.stats()
        self.assertEqual(stats["total"], 60)
        self.assertGreater(stats["archived"] + stats["dormant"], 0, "time advance must move some memories down")
        results = ecology.query(text="北京")
        self.assertTrue(results, "retrieval works at 60+ memories")


if __name__ == "__main__":
    unittest.main()
