"""Embedding provider registry and factory.

Independent from the LLM provider factory: embedding is a separate capability.
Business code only depends on the EmbeddingProvider interface selected by name
in the memory config.
"""

from __future__ import annotations

from typing import Any, Type

from chris.embedding.base import EmbeddingProvider
from chris.embedding.adapters.remote import RemoteEmbeddingProvider
from chris.embedding.mock import MockEmbeddingProvider

_REGISTRY: dict[str, Type[EmbeddingProvider]] = {}


def register(name: str, provider_cls: Type[EmbeddingProvider]) -> None:
    _REGISTRY[name] = provider_cls


def create_embedding_provider(
    provider: str = "mock",
    model: str | None = None,
    dimension: int = 64,
    options: dict[str, Any] | None = None,
) -> EmbeddingProvider:
    """Build an EmbeddingProvider from the memory.embedding config section.

    A missing / unknown provider never crashes Chris: it falls back to the
    offline mock (semantic retrieval then works deterministically in tests and
    offline development; real APIs are opt-in via config).
    """
    provider_cls = _REGISTRY.get(provider)
    if provider_cls is None:
        return MockEmbeddingProvider(model=model or "mock-embedding-v1", dimension=dimension)
    kwargs: dict[str, Any] = dict(options or {})
    if model:
        kwargs.setdefault("model", model)
    kwargs.setdefault("dimension", dimension)
    return provider_cls(**kwargs)


def create_embedding_provider_from_config(config: dict[str, Any] | None) -> EmbeddingProvider:
    """Build from a `memory.embedding` dict ({provider, model, dimension, options})."""
    cfg = config or {}
    return create_embedding_provider(
        provider=str(cfg.get("provider", "mock")),
        model=cfg.get("model"),
        dimension=int(cfg.get("dimension", 64)),
        options=cfg.get("options"),
    )


register("mock", MockEmbeddingProvider)
register("remote", RemoteEmbeddingProvider)
