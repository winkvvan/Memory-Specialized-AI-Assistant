"""Hybrid retriever - union of keyword + semantic (+ recent) candidates.

Semantic retrieval is a CANDIDATE SOURCE, not a replacement for ranking. All
candidates go through the unified ranking (importance / vitality / confidence /
recency / storage) fused with a weighted hybrid relevance:
    relevance = semantic_weight * semantic_score + keyword_weight * keyword_score

When semantic retrieval is unavailable, relevance degrades to keyword_score
(automatic fallback). Retrieval never reinforces memory (access only).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Sequence

from chris.memory.base import MemoryEntry, MemoryResult
from chris.retrieval.keyword import KeywordRetriever

ARCHIVE_TIER = "ARCHIVE"
FORGOTTEN_STATUS = "FORGOTTEN"


class HybridRetriever:
    def __init__(
        self,
        ecology,
        config,
        temporal,
        semantic=None,
        keyword: KeywordRetriever | None = None,
    ) -> None:
        self._ecology = ecology
        self._config = config
        self._temporal = temporal
        self._semantic = semantic
        self._keyword = keyword or KeywordRetriever()
        self.last_debug: dict[str, Any] = {}

    # -- candidate sources ---------------------------------------------

    def _candidate_entries(self, include_archived: bool) -> list[MemoryEntry]:
        return [
            e
            for e in self._ecology.list_all()
            if e.status != FORGOTTEN_STATUS
            and (include_archived or e.storage_tier != ARCHIVE_TIER)
        ]

    def _recent_ids(self) -> list[str]:
        return [e.memory_id for e in self._ecology.list_recent(limit=10)]

    # -- ranking -------------------------------------------------------

    def _effective_life(self, entry: MemoryEntry, now: datetime) -> float:
        remember_override = (
            self._config.remember_half_life_days if entry.metadata.get("remember") else None
        )
        return self._ecology.decay_policy.effective_life(entry, now, remember_override)

    def _rank(
        self,
        entry: MemoryEntry,
        relevance: float,
        keyword_score: float,
        semantic_score: float,
        semantic_available: bool,
        now: datetime,
    ) -> tuple[float, dict[str, float]]:
        cfg = self._config
        vitality = max(0.0, min(1.0, self._effective_life(entry, now) / 100.0))
        last_used = entry.last_used_at or entry.timestamp
        age_days = max(0.0, (now - last_used).total_seconds() / 86400.0)
        recency = 1.0 / (1.0 + age_days / 30.0)
        storage = cfg.storage_factors.get(entry.storage_tier, 0.1)
        score = (
            relevance
            * (0.5 + 0.5 * vitality)
            * (0.4 + 0.6 * entry.importance)
            * (0.4 + 0.6 * entry.confidence)
            * (0.3 + 0.7 * recency)
            * storage
        )
        reason = {
            "relevance": relevance,
            "keyword_score": keyword_score,
            "semantic_score": semantic_score if semantic_available else 0.0,
            "importance": entry.importance,
            "vitality": vitality,
            "confidence": entry.confidence,
            "recency": recency,
            "storage": storage,
        }
        return score, reason

    # -- main query ----------------------------------------------------

    def query(
        self,
        query_text: str,
        limit: int | None = None,
        include_archived: bool = False,
        semantic_only: bool = False,
    ) -> list[MemoryResult]:
        cfg = self._config
        limit = limit or cfg.final_top_k
        needle = query_text.casefold().strip()
        now = self._temporal.now()
        entries = self._candidate_entries(include_archived)

        keyword_scores: dict[str, float] = {}
        if not semantic_only:
            for entry, value in self._keyword.retrieve(query_text, entries, cfg.keyword_top_k):
                keyword_scores[entry.memory_id] = value

        semantic_scores: dict[str, float] = {}
        semantic_available = False
        semantic_error: str | None = None
        if cfg.semantic_enabled and self._semantic is not None:
            try:
                for entry, value in self._semantic.search(
                    query_text, entries, top_k=cfg.semantic_top_k,
                    min_similarity=cfg.semantic_min_similarity,
                ):
                    semantic_scores[entry.memory_id] = value
                semantic_available = not self._semantic.search_failed
                if self._semantic.search_failed:
                    semantic_error = self._semantic.last_error
                    semantic_scores = {}
            except Exception as exc:  # noqa: BLE001
                semantic_error = str(exc)
                semantic_available = False
                semantic_scores = {}

        # union of candidate sources
        candidate_ids: set[str] = set(semantic_scores.keys())
        candidate_ids.update(keyword_scores.keys())
        candidate_ids.update(self._recent_ids())

        results: list[MemoryResult] = []
        final_debug: list[dict[str, Any]] = []
        for memory_id in candidate_ids:
            entry = self._ecology.get(memory_id)
            if entry is None or entry.status == FORGOTTEN_STATUS:
                continue
            keyword_score = keyword_scores.get(memory_id, 0.0)
            semantic_score = semantic_scores.get(memory_id, 0.0)
            if semantic_only and semantic_available:
                relevance = semantic_score
            elif semantic_available:
                relevance = (
                    cfg.semantic_weight * semantic_score
                    + cfg.keyword_weight * keyword_score
                )
            else:
                relevance = keyword_score  # fallback: keyword only
            if relevance <= 0:
                continue
            score, reason = self._rank(
                entry, relevance, keyword_score, semantic_score, semantic_available, now
            )
            results.append(MemoryResult(memory=entry, score=score, reason=reason))
            final_debug.append(
                {
                    "memory_id": memory_id,
                    "content": entry.content,
                    "keyword_score": keyword_score,
                    "semantic_score": semantic_score,
                    "final_score": score,
                    "reason": reason,
                    "status": entry.status,
                    "strength": entry.vitality,
                    "source": {
                        "keyword": memory_id in keyword_scores,
                        "semantic": memory_id in semantic_scores,
                        "recent": memory_id in self._recent_ids(),
                    },
                }
            )

        results.sort(key=lambda r: r.score, reverse=True)
        top = results[:limit]

        self._count_access(top, now)

        self.last_debug = {
            "semantic_enabled": bool(cfg.semantic_enabled and self._semantic is not None),
            "semantic_available": semantic_available,
            "semantic_error": semantic_error,
            "fallback": "keyword" if (cfg.semantic_enabled and not semantic_available) else None,
            "semantic_top_k": cfg.semantic_top_k,
            "keyword_top_k": cfg.keyword_top_k,
            "final_top_k": limit,
            "semantic_weight": cfg.semantic_weight,
            "keyword_weight": cfg.keyword_weight,
            "query": query_text,
            "candidate_ids": sorted(candidate_ids),
            "final": final_debug,
        }
        return top

    def _count_access(self, top: list[MemoryResult], now: datetime) -> None:
        for result in top:
            try:
                self._ecology.access(result.memory.memory_id)
            except Exception:  # noqa: BLE001
                pass
