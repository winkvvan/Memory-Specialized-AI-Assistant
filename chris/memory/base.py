"""Memory System interface and entry type."""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from chris.events import utcnow


def _iso(value: datetime) -> str:
    return value.isoformat()


def _from_iso(value: str | None) -> datetime:
    return datetime.fromisoformat(value) if value else utcnow()


@dataclass
class MemoryEntry:
    memory_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    kind: str = "fact"
    content: str = ""
    importance: float = 0.5
    timestamp: datetime = field(default_factory=utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)
    memory_type: str = ""
    source: str = ""
    last_confirmed_at: datetime | None = None
    last_used_at: datetime | None = None
    life_score: float = 30.0
    confidence: float = 0.7
    reinforcement_count: int = 0
    contradiction_count: int = 0
    status: str = "ACTIVE"
    storage_tier: str = "ACTIVE"
    superseded_by: str | None = None
    supersedes: str | None = None
    compressed_from: list[str] = field(default_factory=list)
    historical_value: float = 0.5
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    last_decay_at: datetime | None = None
    next_decay_at: datetime | None = None
    # -- Memory 2.0 fields ---------------------------------------------
    summary: str = ""
    last_accessed_at: datetime | None = None
    access_count: int = 0
    tags: list[str] = field(default_factory=list)
    related_memories: list[str] = field(default_factory=list)
    conflicts_with: list[str] = field(default_factory=list)
    derived_from: list[str] = field(default_factory=list)
    source_event_id: str = ""
    created_by: str = ""
    version: int = 1
    pinned: bool = False

    @property
    def vitality(self) -> float:
        """Vitality (0..1) = how active this memory is right now.

        Separate from importance (retention value) and confidence (truth).
        life_score (0..100) is the internal storage; vitality = life_score / 100.
        """
        return max(0.0, min(1.0, self.life_score / 100.0))

    @property
    def strength(self) -> float:
        """Memory strength in [0,1]. Alias of vitality: how firmly Chris
        currently holds this memory (driven by time, access and reinforcement)."""
        return self.vitality

    def to_dict(self) -> dict[str, Any]:
        iso = lambda d: _iso(d) if d else None
        return {
            "memory_id": self.memory_id,
            "kind": self.kind,
            "content": self.content,
            "summary": self.summary,
            "importance": self.importance,
            "timestamp": _iso(self.timestamp),
            "metadata": dict(self.metadata),
            "memory_type": self.memory_type,
            "source": self.source,
            "last_confirmed_at": iso(self.last_confirmed_at),
            "last_used_at": iso(self.last_used_at),
            "last_accessed_at": iso(self.last_accessed_at),
            "life_score": self.life_score,
            "confidence": self.confidence,
            "reinforcement_count": self.reinforcement_count,
            "contradiction_count": self.contradiction_count,
            "access_count": self.access_count,
            "status": self.status,
            "storage_tier": self.storage_tier,
            "superseded_by": self.superseded_by,
            "supersedes": self.supersedes,
            "compressed_from": list(self.compressed_from),
            "historical_value": self.historical_value,
            "valid_from": iso(self.valid_from),
            "valid_until": iso(self.valid_until),
            "last_decay_at": iso(self.last_decay_at),
            "next_decay_at": iso(self.next_decay_at),
            "tags": list(self.tags),
            "related_memories": list(self.related_memories),
            "conflicts_with": list(self.conflicts_with),
            "derived_from": list(self.derived_from),
            "source_event_id": self.source_event_id,
            "created_by": self.created_by,
            "version": self.version,
            "pinned": self.pinned,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "MemoryEntry":
        time_keys = (
            "timestamp",
            "last_confirmed_at",
            "last_used_at",
            "last_accessed_at",
            "valid_from",
            "valid_until",
            "last_decay_at",
            "next_decay_at",
        )
        known = {k for k in cls.__dataclass_fields__ if k not in time_keys}
        kwargs: dict[str, Any] = {k: v for k, v in raw.items() if k in known}
        for key in time_keys:
            kwargs[key] = _from_iso(raw.get(key)) if raw.get(key) else None
        return cls(**kwargs)


@dataclass
class MemoryQuery:
    """Contextual retrieval query. Returns ranked MemoryResults."""

    text: str
    limit: int = 5
    include_archived: bool = False
    include_forgotten: bool = False


@dataclass
class MemoryResult:
    memory: MemoryEntry
    score: float
    reason: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory.memory_id,
            "content": self.memory.content,
            "score": round(self.score, 3),
            "reason": {k: round(v, 3) for k, v in self.reason.items()},
        }


class MemoryRepository(ABC):
    """Storage contract for the memory engine.

    The engine never touches JSON/SQLite directly; it only goes through this
    repository. Business layer must not depend on the concrete storage.
    """

    @abstractmethod
    def create(self, entry: MemoryEntry) -> MemoryEntry: ...

    @abstractmethod
    def get(self, memory_id: str) -> MemoryEntry | None: ...

    @abstractmethod
    def update(self, entry: MemoryEntry) -> MemoryEntry: ...

    @abstractmethod
    def delete(self, memory_id: str) -> bool: ...

    @abstractmethod
    def search(self, query: str, limit: int = 5) -> list[MemoryEntry]: ...

    @abstractmethod
    def list_all(self) -> list[MemoryEntry]: ...


class MemorySystem(ABC):
    @abstractmethod
    def add(self, entry: MemoryEntry) -> MemoryEntry: ...

    @abstractmethod
    def get(self, memory_id: str) -> MemoryEntry | None: ...

    @abstractmethod
    def delete(self, memory_id: str) -> bool: ...

    @abstractmethod
    def list_recent(self, limit: int = 10) -> list[MemoryEntry]: ...

    @abstractmethod
    def search(self, query: str, limit: int = 5) -> list[MemoryEntry]: ...
