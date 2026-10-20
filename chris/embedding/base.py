"""Embedding provider interface.

Embedding is an INDEPENDENT capability, decoupled from any LLM provider
(OpenAI / DeepSeek / Qwen / ...). Business code never calls an embedding SDK
directly - it goes through this interface (and its factory).

Swapping an embedding model only means regenerating the embedding cache; it
must never touch memory content or the memory lifecycle.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence


class EmbeddingProvider(ABC):
    """Contract every embedding backend implements."""

    name: str = "abstract"
    model: str = ""
    dimension: int = 0

    @abstractmethod
    def embed(self, text: str) -> list[float]:
        """Return a dense vector for a single text. Deterministic per input."""

    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        """Return vectors for many texts. Default: sequential embed()."""
        return [self.embed(text) for text in texts]

    @abstractmethod
    def health_check(self) -> bool:
        """True when the provider can serve requests right now."""

    def health_report(self) -> dict:
        """Human/debug readable health info (name, model, dimension, ok)."""
        return {"ok": self.health_check(), "name": self.name, "model": self.model,
                "dimension": self.dimension}
