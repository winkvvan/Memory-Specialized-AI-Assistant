"""Embedding subsystem - semantic vector retrieval support for Chris.

Embedding is an independent capability (see base.EmbeddingProvider), fully
decoupled from LLM providers and from the memory lifecycle. It only produces
vectors; it never owns or mutates memories.
"""

from chris.embedding.base import EmbeddingProvider
from chris.embedding.cache import EmbeddingCache, EmbeddingRecord, content_hash
from chris.embedding.factory import (
    create_embedding_provider,
    create_embedding_provider_from_config,
    register,
)
from chris.embedding.mock import MockEmbeddingProvider
from chris.embedding.adapters.remote import RemoteEmbeddingProvider
from chris.embedding.similarity import (
    EmbeddingDimensionError,
    cosine_similarity,
)

__all__ = [
    "EmbeddingProvider",
    "EmbeddingCache",
    "EmbeddingRecord",
    "content_hash",
    "create_embedding_provider",
    "create_embedding_provider_from_config",
    "register",
    "MockEmbeddingProvider",
    "RemoteEmbeddingProvider",
    "EmbeddingDimensionError",
    "cosine_similarity",
]
