"""Retrieval gateway - the business-facing entry point for memory recall.

Orchestrates hybrid retrieval and guarantees Chris keeps working when semantic
retrieval is unavailable:
    SemanticRetriever fails -> HybridRetriever keyword fallback
    HybridRetriever fails   -> MemoryEcology legacy keyword query
It also exposes debugging entry points used by the CLI (/memory search,
/memory embeddings, /memory embedding rebuild).
"""

from __future__ import annotations

from typing import Any

from chris.memory.base import MemoryQuery
from chris.retrieval.hybrid import HybridRetriever

FORGOTTEN_STATUS = "FORGOTTEN"


class RetrievalGateway:
    def __init__(self, ecology, config, temporal, semantic=None, keyword=None) -> None:
        self._ecology = ecology
        self._config = config
        self._temporal = temporal
        self._semantic = semantic
        self._hybrid = HybridRetriever(
            ecology, config, temporal, semantic=semantic, keyword=keyword
        )
        self.last_debug: dict[str, Any] = {}

    # -- query ---------------------------------------------------------

    def query(
        self,
        query_obj: MemoryQuery | None = None,
        text: str = "",
        limit: int | None = None,
        include_archived: bool = False,
        include_forgotten: bool = False,
    ) -> list:
        q = query_obj or MemoryQuery(
            text=text,
            limit=limit or self._config.final_top_k,
            include_archived=include_archived,
            include_forgotten=include_forgotten,
        )
        mode = self._config.retrieval_mode
        if mode == "keyword":
            self.last_debug = {"mode": "keyword", "fallback": None}
            return self._ecology.query(q)
        if mode == "semantic" and self._semantic is not None:
            try:
                results = self._hybrid.query(q.text, q.limit, q.include_archived, semantic_only=True)
                self.last_debug = dict(self._hybrid.last_debug)
                self.last_debug["mode"] = mode
                return results
            except Exception as exc:  # noqa: BLE001
                self.last_debug = {"mode": mode, "error": str(exc), "fallback": "keyword"}
                return self._ecology.query(q)
        try:
            results = self._hybrid.query(q.text, q.limit, q.include_archived)
            self.last_debug = dict(self._hybrid.last_debug)
            self.last_debug["mode"] = "hybrid"
            return results
        except Exception as exc:  # noqa: BLE001
            self.last_debug = {"mode": "hybrid", "error": str(exc), "fallback": "keyword"}
            return self._ecology.query(q)

    def search_entries(self, text: str, limit: int = 5) -> list:
        """Compatibility helper: return ranked MemoryEntry list (not results)."""
        return [r.memory for r in self.query(text=text, limit=limit)]

    # -- debug ---------------------------------------------------------

    def debug_query(self, query_text: str, top_k: int | None = None) -> dict[str, Any]:
        """Run a query and return the full hybrid breakdown for the CLI."""
        self.query(text=query_text, limit=top_k or self._config.final_top_k)
        return dict(self.last_debug)

    def embedding_stats(self) -> dict[str, Any]:
        known = [
            e.memory_id
            for e in self._ecology.list_all()
            if e.status != FORGOTTEN_STATUS
        ]
        stats: dict[str, Any] = {
            "total_memories": len(known),
            "semantic_enabled": bool(self._config.semantic_enabled and self._semantic is not None),
            "retrieval_mode": self._config.retrieval_mode,
        }
        if self._semantic is not None:
            cache_stats = self._semantic._cache.stats(known)
            stats.update(cache_stats)
            stats["semantic_available"] = self._semantic.health()
        else:
            stats["embedded"] = 0
            stats["stale"] = 0
            stats["failed"] = 0
            stats["pending"] = len(known)
            stats["model"] = None
            stats["semantic_available"] = False
        return stats

    def rebuild_embeddings(self, include_archived: bool = False) -> int:
        """Explicit rebuild only - never run automatically at startup."""
        if self._semantic is None:
            return 0
        entries = [
            e
            for e in self._ecology.list_all()
            if e.status != FORGOTTEN_STATUS
            and (include_archived or e.storage_tier != "ARCHIVE")
        ]
        return self._semantic.embed_all(entries)

    def semantic_health(self) -> dict[str, Any]:
        if self._semantic is None:
            return {"available": False, "reason": "no semantic retriever configured"}
        return {
            "available": self._semantic.health(),
            "model": self._semantic.model,
            "last_error": self._semantic.last_error,
        }
