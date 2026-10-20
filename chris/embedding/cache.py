"""Embedding cache - persistent JSON, decoupled from the memory store.

Memory content and embedding cache are SEPARATE. Regenerating embeddings for a
new model never touches the memories themselves. Every record carries a
content hash (invalidated when the memory content changes) and the model /
dimension identity (marked stale when the model changes, because vector spaces
of different models cannot be mixed).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from chris.temporal.system import TemporalSystem

CACHE_SCHEMA_VERSION = 1
STATUS_ACTIVE = "active"
STATUS_STALE = "stale"
STATUS_FAILED = "failed"


def content_hash(content: str) -> str:
    """Hash of the memory content - an embedding is only valid while it matches."""
    return hashlib.sha256((content or "").encode("utf-8")).hexdigest()


@dataclass
class EmbeddingRecord:
    memory_id: str
    embedding: list[float]
    model: str
    dimension: int
    content_hash: str
    status: str = STATUS_ACTIVE
    created_at: str = ""
    updated_at: str = ""
    schema_version: int = CACHE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "embedding": list(self.embedding),
            "model": self.model,
            "dimension": self.dimension,
            "content_hash": self.content_hash,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "schema_version": self.schema_version,
        }


def _iso(value: datetime) -> str:
    return value.isoformat()


class EmbeddingCache:
    """Load / validate / persist embedding records for one model + dimension."""

    def __init__(
        self,
        path: str | Path,
        model: str,
        dimension: int = 0,
        temporal: TemporalSystem | None = None,
    ) -> None:
        self.path = Path(path)
        self.model = model
        self.dimension = int(dimension or 0)
        self._temporal = temporal or TemporalSystem()
        self._records: dict[str, EmbeddingRecord] = {}
        self._dirty = False
        self._load()

    # -- persistence ---------------------------------------------------

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, OSError):
            self._records = {}
            return
        if not isinstance(raw, dict):
            return
        header_model = raw.get("model")
        for item in raw.get("entries", []):
            if not isinstance(item, dict) or not item.get("memory_id"):
                continue
            try:
                record = self._from_item(item)
            except (ValueError, TypeError):
                continue
            model_mismatch = record.model != self.model
            if header_model and header_model != self.model:
                model_mismatch = True
            dim_mismatch = self.dimension and record.dimension != self.dimension
            if model_mismatch or dim_mismatch:
                record.status = STATUS_STALE
            self._records[record.memory_id] = record

    @classmethod
    def _from_item(cls, item: dict[str, Any]) -> EmbeddingRecord:
        embedding = list(item.get("embedding", []))
        if not embedding or any(
            not isinstance(v, (int, float)) for v in embedding
        ):
            raise ValueError("malformed embedding")
        return EmbeddingRecord(
            memory_id=str(item["memory_id"]),
            embedding=[float(v) for v in embedding],
            model=str(item.get("model", "")),
            dimension=int(item.get("dimension", 0)),
            content_hash=str(item.get("content_hash", "")),
            status=str(item.get("status", STATUS_ACTIVE)),
            created_at=str(item.get("created_at", "")),
            updated_at=str(item.get("updated_at", "")),
            schema_version=int(item.get("schema_version", CACHE_SCHEMA_VERSION)),
        )

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, Any] = {
            "schema_version": CACHE_SCHEMA_VERSION,
            "model": self.model,
            "dimension": self.dimension,
            "entries": [
                r.to_dict()
                for r in sorted(self._records.values(), key=lambda r: r.memory_id)
            ],
        }
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        self._dirty = False

    # -- access --------------------------------------------------------

    def get(self, memory_id: str) -> EmbeddingRecord | None:
        return self._records.get(memory_id)

    def get_valid(
        self, memory_id: str, content_hash_value: str, model: str | None = None
    ) -> EmbeddingRecord | None:
        """Return a usable embedding: active, hash matches, model matches."""
        record = self._records.get(memory_id)
        if record is None:
            return None
        if record.status != STATUS_ACTIVE:
            return None
        if record.content_hash != content_hash_value:
            return None
        if model is not None and record.model != model:
            return None
        return record

    def put(self, memory_id: str, embedding: Sequence[float], content_hash_value: str) -> EmbeddingRecord:
        now = _iso(self._temporal.now())
        record = self._records.get(memory_id)
        if record is None:
            record = EmbeddingRecord(
                memory_id=memory_id,
                embedding=[],
                model=self.model,
                dimension=self.dimension,
                content_hash="",
                created_at=now,
            )
        record.embedding = [float(v) for v in embedding]
        record.model = self.model
        record.dimension = len(record.embedding)
        record.content_hash = content_hash_value
        record.status = STATUS_ACTIVE
        record.updated_at = now
        record.schema_version = CACHE_SCHEMA_VERSION
        self._records[memory_id] = record
        self._dirty = True
        return record

    def invalidate(self, memory_id: str) -> None:
        record = self._records.get(memory_id)
        if record is not None:
            record.status = STATUS_STALE
            self._dirty = True

    def mark_failed(self, memory_id: str) -> None:
        record = self._records.get(memory_id)
        if record is not None:
            record.status = STATUS_FAILED
            self._dirty = True

    def remove(self, memory_id: str) -> bool:
        if memory_id in self._records:
            del self._records[memory_id]
            self._dirty = True
            return True
        return False

    def all(self) -> list[EmbeddingRecord]:
        return list(self._records.values())

    def stats(self, known_memory_ids: Sequence[str] | None = None) -> dict[str, Any]:
        records = list(self._records.values())
        active = sum(1 for r in records if r.status == STATUS_ACTIVE)
        stale = sum(1 for r in records if r.status == STATUS_STALE)
        failed = sum(1 for r in records if r.status == STATUS_FAILED)
        pending: int | None = None
        if known_memory_ids is not None:
            active_ids = {r.memory_id for r in records if r.status == STATUS_ACTIVE}
            pending = sum(1 for mid in known_memory_ids if mid not in active_ids)
        return {
            "cache_records": len(records),
            "embedded": active,
            "stale": stale,
            "failed": failed,
            "pending": pending,
            "model": self.model,
            "dimension": self.dimension,
            "path": str(self.path),
            "dirty": self._dirty,
        }
