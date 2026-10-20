"""Retrieval subsystem - candidate finding + unified ranking.

Decoupled from the memory lifecycle: retrieval only FINDS memories and ranks
them; it never reinforces, decays or archives. Semantic candidates come from
the embedding subsystem; keyword candidates are deterministic overlap; the
hybrid retriever unions them and feeds the legacy ranking factors.
"""

from chris.retrieval.gateway import RetrievalGateway
from chris.retrieval.hybrid import HybridRetriever
from chris.retrieval.keyword import KeywordRetriever
from chris.retrieval.semantic import SemanticRetriever

__all__ = [
    "RetrievalGateway",
    "HybridRetriever",
    "KeywordRetriever",
    "SemanticRetriever",
]
