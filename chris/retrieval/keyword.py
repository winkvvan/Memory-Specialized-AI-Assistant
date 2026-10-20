"""Deterministic keyword / character-overlap retriever.

Independent layer over MemoryEntry objects. The algorithm mirrors the legacy
keyword relevance used by MemoryEcology (substring / tag / char-overlap), kept
here as the canonical standalone copy so the hybrid retriever can score
keyword relevance without reaching into ecology internals.
"""

from __future__ import annotations

import re
from typing import Sequence

from chris.memory.base import MemoryEntry

_SPLIT = re.compile(r"[\s，。,.!！？?、；;：:'\"“”‘’()（）\-\t\n]+")


def normalize(text: str) -> str:
    return _SPLIT.sub("", text.casefold())


class KeywordRetriever:
    """Scores memories against a query by deterministic keyword overlap."""

    def score(self, entry: MemoryEntry, needle: str) -> float:
        if not needle:
            return 0.0
        haystack = (entry.content + " " + (entry.summary or "")).casefold()
        if needle in haystack:
            return 1.0
        for tag in list(entry.tags) + list(entry.metadata.get("tags", [])):
            if needle in str(tag).casefold():
                return 0.9
        query_chars = set(normalize(needle))
        content_chars = set(normalize(entry.content))
        if not query_chars:
            return 0.0
        overlap = len(query_chars & content_chars) / len(query_chars)
        if overlap >= 0.4:
            return 0.5 + 0.5 * overlap
        return 0.0

    def retrieve(
        self, query_text: str, entries: Sequence[MemoryEntry], top_k: int
    ) -> list[tuple[MemoryEntry, float]]:
        """Top-K entries by keyword score (scores > 0 only)."""
        needle = query_text.casefold().strip()
        if not needle:
            return []
        scored = []
        for entry in entries:
            value = self.score(entry, needle)
            if value > 0:
                scored.append((entry, value))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:top_k]
