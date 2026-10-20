"""Semantic retriever - embedding-based candidate finding.

Only finds candidates. It never mutates memory lifecycle (no access count, no
decay, no reinforcement). Embeddings are generated lazily on first need and
kept in the EmbeddingCache keyed by content hash + model identity.
"""

from __future__ import annotations

from typing import Sequence

from chris.embedding.cache import EmbeddingCache, content_hash
from chris.embedding.similarity import EmbeddingDimensionError, cosine_similarity
from chris.memory.base import MemoryEntry


class SemanticRetriever:
    def __init__(
        self,
        provider,
        cache: EmbeddingCache,
        min_similarity: float = 0.25,
        top_k: int = 20,
        generate_cap: int = 5000,
        batch_size: int = 64,
    ) -> None:
        self._provider = provider
        self._cache = cache
        self._min_similarity = min_similarity
        self._top_k = top_k
        self._generate_cap = generate_cap
        self._batch_size = batch_size
        self.last_error: str | None = None
        self.search_failed = False
        self.generated_this_search = 0

    # -- status --------------------------------------------------------

    def health(self) -> bool:
        try:
            return bool(self._provider.health_check())
        except Exception:  # noqa: BLE001
            return False

    @property
    def model(self) -> str:
        return getattr(self._provider, "model", "")

    # -- embedding -----------------------------------------------------

    def embed_query(self, text: str) -> list[float] | None:
        try:
            return self._provider.embed(text)
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"embed query failed: {exc}"
            return None

    def embed_all(self, entries: Sequence[MemoryEntry]) -> int:
        """Explicit: generate + persist embeddings for every entry. Returns count."""
        texts = [e.content for e in entries]
        vectors = self._provider.embed_batch(texts)
        count = 0
        for entry, vector in zip(entries, vectors):
            if not vector:
                continue
            self._cache.put(entry.memory_id, vector, content_hash(entry.content))
            count += 1
        self._cache.save()
        return count

    def _ensure_embeddings(self, entries: Sequence[MemoryEntry]) -> None:
        missing = [
            e
            for e in entries
            if self._cache.get_valid(e.memory_id, content_hash(e.content), self.model) is None
        ]
        if not missing:
            return
        missing.sort(key=lambda e: (e.timestamp, e.memory_id), reverse=True)
        targets = missing[: self._generate_cap]
        self.generated_this_search = 0
        for start in range(0, len(targets), self._batch_size):
            chunk = targets[start : start + self._batch_size]
            texts = [e.content for e in chunk]
            vectors = self._provider.embed_batch(texts)
            for entry, vector in zip(chunk, vectors):
                if not vector:
                    continue
                self._cache.put(entry.memory_id, vector, content_hash(entry.content))
                self.generated_this_search += 1
        self._cache.save()

    # -- search --------------------------------------------------------

    def search(
        self,
        query_text: str,
        entries: Sequence[MemoryEntry],
        top_k: int | None = None,
        min_similarity: float | None = None,
    ) -> list[tuple[MemoryEntry, float]]:
        """Return up to top_k (entry, semantic_score) candidates.

        Returns [] when semantic retrieval is unavailable (provider failure or
        no valid query embedding) - callers fall back to keyword retrieval.
        """
        self.generated_this_search = 0
        self.search_failed = False
        query_vector = self.embed_query(query_text)
        if query_vector is None:
            self.search_failed = True
            return []
        try:
            self._ensure_embeddings(entries)
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"embedding generation failed: {exc}"
            self.search_failed = True
            return []
        threshold = min_similarity if min_similarity is not None else self._min_similarity
        limit = top_k or self._top_k
        scored: list[tuple[MemoryEntry, float]] = []
        for entry in entries:
            record = self._cache.get_valid(
                entry.memory_id, content_hash(entry.content), self.model
            )
            if record is None:
                continue
            try:
                similarity = cosine_similarity(query_vector, record.embedding)
            except (ValueError, EmbeddingDimensionError) as exc:  # noqa: BLE001
                self.last_error = f"similarity failed for {entry.memory_id}: {exc}"
                continue
            if similarity < threshold:
                continue
            scored.append((entry, similarity))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:limit]
