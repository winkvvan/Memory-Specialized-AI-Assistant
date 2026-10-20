"""Deterministic mock embedding provider - no API key, offline.

Used for unit tests, CI and offline development. Produces a fixed-dimension
vector from: character hashing bag + a small synonym-term table (simulates a
model that understands common concept clusters like 开发/项目 or AI/模型).
This lets the semantic retriever be tested end to end without any network.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Sequence

from chris.embedding.base import EmbeddingProvider

_TERMS: dict[str, tuple[int, ...]] = {
    # development / project concept
    "开发": (0,), "项目": (0,), "编程": (0,), "软件": (0,), "工程": (0,),
    "dev": (0,), "build": (0,), "project": (0,), "coding": (0,),
    # ai / model concept
    "ai": (1,), "人工智能": (1,), "llm": (1,), "模型": (1,), "大模型": (1,),
    # chris identity
    "chris": (2,), "克里斯": (2,),
    # coffee / drink
    "咖啡": (3,), "coffee": (3,), "饮品": (3,),
    # preference
    "喜欢": (4,), "偏好": (4,), "prefer": (4,),
}

_SPLIT = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")


def _normalize(text: str) -> str:
    return _SPLIT.sub("", text.casefold())


class MockEmbeddingProvider(EmbeddingProvider):
    name = "mock"

    def __init__(
        self,
        model: str = "mock-embedding-v1",
        dimension: int = 64,
        synonym_groups: dict[str, tuple[int, ...]] | None = None,
        **kwargs: object,
    ) -> None:
        self.model = model
        self.dimension = max(8, int(dimension))
        self._terms = dict(_TERMS)
        if synonym_groups:
            self._terms.update(synonym_groups)
        self._term_weight = 4.0
        self._char_weight = 0.5

    def _seed_dim(self, ch: str) -> int:
        digest = hashlib.sha256((self.model + "\x00" + ch).encode("utf-8")).hexdigest()
        return int(digest[:8], 16) % self.dimension

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dimension
        normalized = _normalize(text)
        if not normalized:
            return [0.0] * self.dimension
        for ch in normalized:
            vec[self._seed_dim(ch)] += self._char_weight
        for term, dims in self._terms.items():
            if term in normalized:
                for dim in dims:
                    vec[dim] += self._term_weight
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0.0:
            return [0.0] * self.dimension
        return [round(v / norm, 6) for v in vec]

    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]

    def health_check(self) -> bool:
        return True

    def health_report(self) -> dict:
        return {"ok": True, "name": self.name, "model": self.model,
                "dimension": self.dimension}
