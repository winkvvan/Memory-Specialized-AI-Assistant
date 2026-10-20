"""Phase 7.5 - Semantic Memory Retrieval tests.

Covers: MockEmbeddingProvider, EmbeddingCache (hash/model identity, lazy
generation, cache hit), content-hash invalidation, dimension safety, cosine
similarity, semantic / keyword / hybrid retrieval, hybrid ranking, access vs
reinforcement, archive handling, provider failure fallback, restart
persistence, provider independence, and a minimal scale test.

Embedding is decoupled from the memory lifecycle: retrieval only finds and
ranks; it never reinforces, decays or archives by itself.
"""

import json
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from chris.embedding.cache import EmbeddingCache, content_hash
from chris.embedding.mock import MockEmbeddingProvider
from chris.embedding.similarity import (
    EmbeddingDimensionError,
    cosine_similarity,
)
from chris.events import EventBus
from chris.memory.base import MemoryEntry
from chris.memory.ecology import MemoryConfig, MemoryEcology, MemoryType, StorageTier
from chris.retrieval.gateway import RetrievalGateway
from chris.retrieval.keyword import KeywordRetriever
from chris.retrieval.semantic import SemanticRetriever
from chris.temporal.clock import ManualClock
from chris.temporal.system import TemporalSystem

BASE = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def fact(content, memory_type="", metadata=None):
    return MemoryEntry(
        kind="fact",
        content=content,
        memory_type=memory_type,
        metadata={"skip_dedup": True, **(metadata or {})},
    )


def make_gateway(tmp, memory_config=None, provider=None, model="mock-embedding-v1", dimension=64):
    clock = ManualClock(BASE)
    temporal = TemporalSystem(clock=clock)
    bus = EventBus()
    cfg = memory_config or MemoryConfig()
    path = Path(tmp) / "m.json"
    ecology = MemoryEcology(path, event_bus=bus, temporal=temporal, config=cfg)
    prov = provider or MockEmbeddingProvider(model=model, dimension=dimension)
    cache = EmbeddingCache(
        Path(tmp) / "m.embeddings.json", model=prov.model,
        dimension=getattr(prov, "dimension", dimension), temporal=temporal,
    )
    semantic = SemanticRetriever(
        prov, cache, min_similarity=cfg.semantic_min_similarity,
        top_k=cfg.semantic_top_k, generate_cap=cfg.embedding_generate_cap,
        batch_size=cfg.embedding_batch_size,
    )
    gateway = RetrievalGateway(ecology, cfg, temporal, semantic=semantic)
    return gateway, ecology, clock, cache, prov, cfg


class CountingProvider(MockEmbeddingProvider):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.embed_calls = 0
        self.batch_calls = 0

    def embed(self, text):
        self.embed_calls += 1
        return super().embed(text)

    def embed_batch(self, texts):
        self.batch_calls += 1
        return super().embed_batch(texts)


class _FailingProvider:
    name = "fail"
    model = "fail-model"
    dimension = 64

    def embed(self, text):
        raise RuntimeError("embedding API down")

    def embed_batch(self, texts):
        raise RuntimeError("embedding API down")

    def health_check(self):
        return False


class TestMockEmbeddingProvider(unittest.TestCase):
    def test_offline_no_key_deterministic(self):
        provider = MockEmbeddingProvider()
        self.assertTrue(provider.health_check())  # no API key required
        vec = provider.embed("用户喜欢冰美式")
        self.assertEqual(len(vec), provider.dimension)
        self.assertEqual(provider.embed("用户喜欢冰美式"), vec)  # deterministic

    def test_different_models_differ(self):
        a = MockEmbeddingProvider(model="mock-a").embed("你好")
        b = MockEmbeddingProvider(model="mock-b").embed("你好")
        self.assertNotEqual(a, b)

    def test_paraphrase_concept_similar(self):
        provider = MockEmbeddingProvider(dimension=64)
        sim = cosine_similarity(
            provider.embed("用户最近一直在开发 Chris"),
            provider.embed("之前那个 AI 项目怎么样了"),
        )
        self.assertGreater(sim, 0.3)


class TestCosineSimilarity(unittest.TestCase):
    def test_same_orthogonal_zero(self):
        self.assertEqual(cosine_similarity([1, 0, 0], [1, 0, 0]), 1.0)
        self.assertEqual(cosine_similarity([1, 0, 0], [0, 1, 0]), 0.0)
        self.assertEqual(cosine_similarity([0, 0, 0], [0, 0, 0]), 0.0)

    def test_dimension_mismatch_raises(self):
        with self.assertRaises(EmbeddingDimensionError):
            cosine_similarity([1, 0], [1, 0, 0])

    def test_malformed_input_raises(self):
        with self.assertRaises(ValueError):
            cosine_similarity([], [1, 0])
        with self.assertRaises(ValueError):
            cosine_similarity([1, "x"], [1, 2])


class TestEmbeddingCache(unittest.TestCase):
    def test_same_content_cache_hit(self):
        with tempfile.TemporaryDirectory() as tmp:
            gateway, ecology, _c, cache, provider, _cfg = make_gateway(
                tmp, provider=CountingProvider()
            )
            e1 = ecology.add(fact("用户喜欢冰美式"))
            ecology.add(fact("用户喜欢咖啡"))
            gateway.query(text="咖啡")
            first_batch = provider.batch_calls
            self.assertGreaterEqual(provider.batch_calls, 1)
            # second search: memory embeddings already cached -> no regeneration
            gateway.query(text="冰美式")
            self.assertEqual(provider.batch_calls, first_batch, "embedding must be reused from cache")
            record = cache.get_valid(e1.memory_id, content_hash(e1.content), provider.model)
            self.assertIsNotNone(record)

    def test_content_hash_invalidation(self):
        with tempfile.TemporaryDirectory() as tmp:
            gateway, ecology, _c, cache, provider, _cfg = make_gateway(
                tmp, provider=CountingProvider()
            )
            e = ecology.add(fact("用户喜欢冰美式"))
            gateway.query(text="咖啡")
            old_hash = cache.get(e.memory_id).content_hash
            e.content = "用户现在改喝无糖拿铁了"
            ecology.update(e)
            gateway.query(text="拿铁")
            new_record = cache.get(e.memory_id)
            self.assertNotEqual(new_record.content_hash, old_hash)
            self.assertEqual(new_record.content_hash, content_hash(e.content))

    def test_dimension_mismatch_fails_safely(self):
        with tempfile.TemporaryDirectory() as tmp:
            gateway, ecology, _c, cache, _prov, _cfg = make_gateway(tmp)
            e = ecology.add(fact("用户喜欢冰美式"))
            gateway.query(text="咖啡")  # embed with 64 dim
            record = cache.get(e.memory_id)
            record.embedding = [0.5] * 16  # corrupt dimension
            record.dimension = 16
            # must not crash - record is skipped
            results = gateway.query(text="咖啡")
            self.assertNotIn(e.memory_id, [r.memory.memory_id for r in results])


class TestSemanticRetrieval(unittest.TestCase):
    def test_paraphrase_enters_semantic_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            gateway, ecology, _c, _cache, _prov, _cfg = make_gateway(tmp)
            ecology.add(fact("用户最近一直在开发 Chris"))
            ecology.add(fact("用户喜欢冰美式"))
            debug = gateway.debug_query("之前那个 AI 项目怎么样了？")
            self.assertTrue(debug["semantic_enabled"])
            hits = [d for d in debug["final"] if d["semantic_score"] > 0]
            self.assertTrue(
                any("开发 Chris" in d["content"] for d in hits),
                "semantic candidate should include the paraphrased memory",
            )

    def test_keyword_exact_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            gateway, ecology, _c, _cache, _prov, _cfg = make_gateway(tmp)
            ecology.add(fact("CosyVoice2-0.5B 是语音合成模型"))
            debug = gateway.debug_query("CosyVoice2-0.5B")
            self.assertTrue(
                any("CosyVoice2" in d["content"] and d["keyword_score"] >= 1.0 for d in debug["final"])
            )

    def test_hybrid_union(self):
        with tempfile.TemporaryDirectory() as tmp:
            gateway, ecology, _c, _cache, _prov, _cfg = make_gateway(tmp)
            ecology.add(fact("用户最近一直在开发 Chris"))
            ecology.add(fact("CosyVoice2-0.5B 是语音合成模型"))
            debug = gateway.debug_query("那个AI项目 CosyVoice2")
            final = debug["final"]
            self.assertTrue(any("开发 Chris" in d["content"] for d in final))
            self.assertTrue(any("CosyVoice2" in d["content"] for d in final))
            self.assertTrue(any(d["semantic_score"] > 0 for d in final))
            self.assertTrue(any(d["keyword_score"] > 0 for d in final))

    def test_hybrid_ranking_importance_strength_beats_recency(self):
        with tempfile.TemporaryDirectory() as tmp:
            gateway, ecology, clock, _cache, _prov, cfg = make_gateway(
                tmp, memory_config=MemoryConfig(semantic_enabled=False)
            )
            e1 = ecology.add(fact("用户喜欢咖啡", MemoryType.SEMANTIC.value))
            e1.importance = 0.9
            e1.life_score = 90.0
            clock.advance(timedelta(days=30))
            e2 = ecology.add(fact("用户今天喝了咖啡", MemoryType.SEMANTIC.value))
            e2.importance = 0.1
            e2.life_score = 20.0
            results = gateway.query(text="咖啡")
            self.assertEqual(results[0].memory.memory_id, e1.memory_id)
            self.assertIn("keyword_score", results[0].reason)
            self.assertIn("semantic_score", results[0].reason)


class TestAccessVsReinforcement(unittest.TestCase):
    def test_semantic_recall_counts_access_not_strength(self):
        with tempfile.TemporaryDirectory() as tmp:
            gateway, ecology, _c, _cache, _prov, _cfg = make_gateway(tmp)
            e = ecology.add(fact("用户喜欢冰美式"))
            before_life = ecology.get(e.memory_id).life_score
            before_access = ecology.get(e.memory_id).access_count
            gateway.query(text="冰美式")
            after = ecology.get(e.memory_id)
            self.assertGreater(after.access_count, before_access)
            self.assertEqual(after.life_score, before_life, "recall must not change strength")

    def test_only_reinforce_boosts_strength(self):
        with tempfile.TemporaryDirectory() as tmp:
            gateway, ecology, _c, _cache, _prov, _cfg = make_gateway(tmp)
            e = ecology.add(fact("用户喜欢冰美式"))
            before = ecology.get(e.memory_id).life_score
            gateway.query(text="冰美式")
            self.assertEqual(ecology.get(e.memory_id).life_score, before)
            ecology.reinforce(e.memory_id)
            self.assertGreater(ecology.get(e.memory_id).life_score, before)


class TestArchive(unittest.TestCase):
    def test_archived_excluded_by_default_included_when_asked(self):
        with tempfile.TemporaryDirectory() as tmp:
            gateway, ecology, _c, _cache, _prov, _cfg = make_gateway(tmp)
            e = ecology.add(fact("用户喜欢冰美式"))
            ecology.archive_one(e.memory_id)
            self.assertEqual(ecology.get(e.memory_id).storage_tier, StorageTier.ARCHIVE.value)
            default = gateway.query(text="冰美式", include_archived=False)
            self.assertNotIn(e.memory_id, [r.memory.memory_id for r in default])
            included = gateway.query(text="冰美式", include_archived=True)
            self.assertIn(e.memory_id, [r.memory.memory_id for r in included])


class TestProviderFailure(unittest.TestCase):
    def test_failure_falls_back_to_keyword(self):
        with tempfile.TemporaryDirectory() as tmp:
            gateway, ecology, _c, _cache, _prov, _cfg = make_gateway(
                tmp, provider=_FailingProvider()
            )
            ecology.add(fact("用户喜欢冰美式"))
            results = gateway.query(text="冰美式")
            self.assertTrue(results, "keyword fallback must still answer")
            self.assertFalse(gateway.last_debug.get("semantic_available", True))
            self.assertEqual(gateway.last_debug.get("fallback"), "keyword")


class TestPersistenceAndIndependence(unittest.TestCase):
    def test_restart_memory_and_cache_consistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            gw1, eco1, _c, _cache1, prov, cfg = make_gateway(tmp)
            e = eco1.add(fact("用户喜欢冰美式"))
            gw1.query(text="冰美式")
            # second runtime on the same files
            clock2 = ManualClock(BASE)
            temporal2 = TemporalSystem(clock=clock2)
            eco2 = MemoryEcology(Path(tmp) / "m.json", temporal=temporal2, config=cfg)
            cache2 = EmbeddingCache(
                Path(tmp) / "m.embeddings.json", model=prov.model, dimension=prov.dimension, temporal=temporal2
            )
            sem2 = SemanticRetriever(prov, cache2, min_similarity=cfg.semantic_min_similarity)
            gw2 = RetrievalGateway(eco2, cfg, temporal2, semantic=sem2)
            results = gw2.query(text="冰美式")
            self.assertIn(e.memory_id, [r.memory.memory_id for r in results])
            self.assertIsNotNone(
                cache2.get_valid(e.memory_id, content_hash(e.content), prov.model),
                "embedding cache must survive restart",
            )

    def test_swap_embedding_model_does_not_break_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            gw1, eco1, _c, _cache1, _prov_a, cfg = make_gateway(tmp, model="mock-model-a")
            e = eco1.add(fact("用户喜欢冰美式"))
            gw1.query(text="冰美式")
            state_before = eco1.stats()["total"]
            # new runtime with a different embedding model on the same memory
            clock2 = ManualClock(BASE)
            temporal2 = TemporalSystem(clock=clock2)
            eco2 = MemoryEcology(Path(tmp) / "m.json", temporal=temporal2, config=cfg)
            prov_b = MockEmbeddingProvider(model="mock-model-b", dimension=64)
            cache2 = EmbeddingCache(
                Path(tmp) / "m.embeddings.json", model=prov_b.model, dimension=64, temporal=temporal2
            )
            self.assertEqual(cache2.get(e.memory_id).status, "stale", "old model vectors must be marked stale")
            sem2 = SemanticRetriever(prov_b, cache2, min_similarity=cfg.semantic_min_similarity)
            gw2 = RetrievalGateway(eco2, cfg, temporal2, semantic=sem2)
            self.assertEqual(eco2.stats()["total"], state_before, "memory itself must be untouched")
            results = gw2.query(text="冰美式")
            self.assertIn(e.memory_id, [r.memory.memory_id for r in results])
            self.assertEqual(cache2.get(e.memory_id).model, "mock-model-b")


class TestKeywordRetrieverUnit(unittest.TestCase):
    def test_keyword_score(self):
        retriever = KeywordRetriever()
        entry = MemoryEntry(content="CosyVoice2-0.5B 是语音模型")
        self.assertEqual(retriever.score(entry, "CosyVoice2-0.5B"), 1.0)
        self.assertEqual(retriever.score(entry, "不相关"), 0.0)


class TestPerformance(unittest.TestCase):
    def test_scales_100_1000_5000(self):
        """Retrieval scaling only - builds the store in ONE write so the JSON
        per-add O(N^2) cost (a known, documented migration candidate) does not
        dominate. No obvious super-linear blowup expected in retrieval."""
        sizes = (100, 1000, 5000)
        times = {}
        for n in sizes:
            with tempfile.TemporaryDirectory() as tmp:
                gateway, ecology, _c, _cache, _prov, cfg = make_gateway(
                    tmp, memory_config=MemoryConfig(embedding_generate_cap=5000)
                )
                _bulk_fill(ecology, n)
                t0 = time.perf_counter()
                kw = gateway.query(text="主题42", limit=5)
                t_kw = time.perf_counter() - t0
                t0 = time.perf_counter()
                hy = gateway.query(text="用户在第42阶段提过关于主题42的细节", limit=5)
                t_hy = time.perf_counter() - t0
                t0 = time.perf_counter()
                se = gateway.query(text="关于主题42的细节", limit=5)
                t_se = time.perf_counter() - t0
                self.assertTrue(kw and hy and se, "all retrieval modes must return results")
                times[n] = (t_kw, t_hy, t_se)
        # no obvious exponential blowup: 5000 hybrid not 8x slower than 1000
        self.assertLess(times[5000][1], max(30.0, 8.0 * times[1000][1]))
        # cached semantic search stays fast at scale
        self.assertLess(times[5000][2], 30.0)


def _bulk_fill(ecology, n: int) -> None:
    """Insert n memories without a per-add JSON write (test-only bulk fill)."""
    now = ecology._temporal.now()
    entries = []
    for i in range(n):
        ts = now - timedelta(days=n - i)
        entry = MemoryEntry(
            kind="fact",
            content=f"用户在第{i}阶段提过关于主题{i}的细节",
            memory_type=MemoryType.EPISODIC.value,
            timestamp=ts,
        )
        entry.last_decay_at = ts
        entry.next_decay_at = ts + timedelta(days=1)
        entry.last_used_at = ts
        entry.last_accessed_at = ts
        entry.life_score = 30.0
        entries.append(entry.to_dict())
    data = {"schema_version": 2, "entries": entries}
    ecology.path.parent.mkdir(parents=True, exist_ok=True)
    ecology.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    ecology._load()


if __name__ == "__main__":
    unittest.main()
