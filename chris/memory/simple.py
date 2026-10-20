"""Simple JSON-file backed memory.

Retrieval uses plain keyword matching. No complex algorithms by design
(project principle: simple, reliable, testable).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from chris.events import Event, EventBus, EventType
from chris.memory.base import MemoryEntry, MemorySystem


class SimpleFileMemory(MemorySystem):
    def __init__(self, path: Path, event_bus: EventBus | None = None) -> None:
        self.path = path
        self._bus = event_bus
        self._entries: dict[str, MemoryEntry] = {}
        self._load()

    def add(self, entry: MemoryEntry) -> MemoryEntry:
        self._entries[entry.memory_id] = entry
        self._save()
        if self._bus:
            self._bus.publish(
                Event(
                    EventType.MEMORY_CREATED,
                    "memory",
                    {"memory_id": entry.memory_id, "kind": entry.kind},
                )
            )
        return entry

    def get(self, memory_id: str) -> MemoryEntry | None:
        return self._entries.get(memory_id)

    def delete(self, memory_id: str) -> bool:
        removed = self._entries.pop(memory_id, None) is not None
        if removed:
            self._save()
        return removed

    def list_recent(self, limit: int = 10) -> list[MemoryEntry]:
        ordered = sorted(
            self._entries.values(),
            key=lambda e: (e.timestamp, e.memory_id),
            reverse=True,
        )
        return ordered[:limit]

    def search(self, query: str, limit: int = 5) -> list[MemoryEntry]:
        needle = query.casefold()
        scored: list[tuple[int, datetime, MemoryEntry]] = []
        for entry in self._entries.values():
            score = 0
            if needle in entry.content.casefold():
                score += 2
            for tag in entry.metadata.get("tags", []):
                if needle in str(tag).casefold():
                    score += 1
            if score:
                scored.append((score, entry.timestamp, entry))
        scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
        return [entry for _, _, entry in scored[:limit]]

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, OSError):
            return
        for item in raw:
            entry = MemoryEntry.from_dict(item)
            self._entries[entry.memory_id] = entry

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        ordered = sorted(
            self._entries.values(), key=lambda e: (e.timestamp, e.memory_id)
        )
        data = [entry.to_dict() for entry in ordered]
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
