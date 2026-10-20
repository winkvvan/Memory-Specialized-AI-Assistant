"""Memory Ecology 2.0 - the memory life system.

Memories are NOT simply "keep forever / delete at zero". Every memory has a
lifecycle: born, active, reinforced, stable, weakening, dormant, archived,
compressed, and only under strict conditions garbage-collected.

Key principle: **Memory Life Score is not Memory Retention.**
  life_score   -> how active a memory is right now   (0..100)
  importance   -> how much it is worth keeping long term (0..1)
  confidence   -> how much Chris trusts it            (0..1)
  storage_tier -> which tier it lives in (ACTIVE/DORMANT/ARCHIVE)

Memory never reads the system clock directly: it asks the Temporal Engine for
elapsed time, so a virtual clock can drive the whole lifecycle in tests.
"""

from __future__ import annotations

import json
import shutil
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

from chris.events import Event, EventBus, EventType
from chris.memory.base import (
    MemoryEntry,
    MemoryQuery,
    MemoryRepository,
    MemoryResult,
    MemorySystem,
)
from chris.temporal.system import TemporalSystem


class MemoryStatus(str, Enum):
    BORN = "BORN"
    ACTIVE = "ACTIVE"
    REINFORCED = "REINFORCED"
    STABLE = "STABLE"
    WEAKENING = "WEAKENING"
    DORMANT = "DORMANT"
    ARCHIVED = "ARCHIVED"
    RECOVERED = "RECOVERED"
    SUPERSEDED = "SUPERSEDED"
    INVALIDATED = "INVALIDATED"
    FORGOTTEN = "FORGOTTEN"


class StorageTier(str, Enum):
    ACTIVE = "ACTIVE"
    DORMANT = "DORMANT"
    ARCHIVE = "ARCHIVE"


class MemorySource(str, Enum):
    EXPLICIT_USER = "EXPLICIT_USER"
    USER_STATEMENT = "USER_STATEMENT"
    OBSERVED_BEHAVIOR = "OBSERVED_BEHAVIOR"
    SYSTEM_INFERENCE = "SYSTEM_INFERENCE"
    LLM_INFERENCE = "LLM_INFERENCE"


class MemoryType(str, Enum):
    # canonical Memory 2.0 taxonomy
    EPISODIC = "EPISODIC"
    SEMANTIC = "SEMANTIC"
    PREFERENCE = "PREFERENCE"
    RELATIONSHIP = "RELATIONSHIP"
    BEHAVIORAL = "BEHAVIORAL"
    PROJECT = "PROJECT"
    TEMPORARY = "TEMPORARY"
    # legacy aliases kept for backward compatibility
    IDENTITY = "IDENTITY"
    EVENT = "EVENT"
    PLAN = "PLAN"
    TEMPORARY_STATE = "TEMPORARY_STATE"
    INFERENCE = "INFERENCE"
    FACT = "FACT"


_KIND_TO_TYPE = {
    "conversation": MemoryType.EPISODIC.value,
    "fact": MemoryType.SEMANTIC.value,
    "note": MemoryType.SEMANTIC.value,
}


def kind_to_type(kind: str) -> str:
    return _KIND_TO_TYPE.get(kind, MemoryType.SEMANTIC.value)


def _default_source(entry: MemoryEntry) -> str:
    if entry.content.startswith("user:"):
        return MemorySource.USER_STATEMENT.value
    if entry.content.startswith("chris:"):
        return MemorySource.SYSTEM_INFERENCE.value
    return MemorySource.EXPLICIT_USER.value


@dataclass
class MemoryConfig:
    active_limit: int = 5000
    archive_limit: int = 100000
    compression_threshold: int = 365
    gc_enabled: bool = True

    stable_life_threshold: float = 60.0
    dormant_life_threshold: float = 15.0
    archive_after_days: int = 180

    max_life_score: float = 100.0
    min_life_score: float = 0.0
    reinforce_boost: float = 8.0
    reinforce_cap: float = 90.0
    reinforce_diminish: float = 0.85

    remember_importance: float = 0.99
    remember_min_life: float = 70.0
    remember_half_life_days: float = 1825.0  # ~5 years: much more memorable, not eternal

    # vitality-driven lifecycle thresholds (vitality = life_score / 100)
    archive_life_threshold: float = 10.0   # vitality < 0.10 -> ARCHIVED (with inactivity)
    dedup_similarity: float = 0.75         # token-overlap threshold for MERGE (short content only)
    dedup_max_len: int = 10                # only dedup short statements (avoid long-template merges)
    memory_prompt_budget: int = 5          # max memories injected into the prompt
    schema_version: int = 2
    maintenance_interval_seconds: int = 300  # MemoryMaintenance cadence (configurable)

    # per-type half-life overrides (days); merged over type_params
    decay: dict[str, float] = field(default_factory=dict)

    # -- semantic retrieval (7.5) --------------------------------------
    # retrieval is a separate layer (chris/retrieval); these only tune it.
    retrieval_mode: str = "hybrid"          # hybrid | semantic | keyword
    semantic_enabled: bool = True
    semantic_top_k: int = 20
    keyword_top_k: int = 20
    final_top_k: int = 5
    semantic_weight: float = 0.6
    keyword_weight: float = 0.4
    semantic_min_similarity: float = 0.25
    embedding_provider: str = "mock"
    embedding_model: str = "mock-embedding-v1"
    embedding_dimension: int = 64
    embedding_batch_size: int = 64
    embedding_generate_cap: int = 5000

    source_weights: dict[str, float] = field(
        default_factory=lambda: {
            "EXPLICIT_USER": 0.98,
            "USER_STATEMENT": 0.9,
            "OBSERVED_BEHAVIOR": 0.8,
            "SYSTEM_INFERENCE": 0.6,
            "LLM_INFERENCE": 0.5,
        }
    )

    type_params: dict[str, dict[str, float]] = field(
        default_factory=lambda: {
            "SEMANTIC": {"initial_life": 60.0, "half_life_days": 365.0, "importance": 0.6},
            "RELATIONSHIP": {"initial_life": 55.0, "half_life_days": 365.0, "importance": 0.85},
            "PREFERENCE": {"initial_life": 50.0, "half_life_days": 180.0, "importance": 0.7},
            "EPISODIC": {"initial_life": 30.0, "half_life_days": 30.0, "importance": 0.3},
            "BEHAVIORAL": {"initial_life": 45.0, "half_life_days": 120.0, "importance": 0.55},
            "PROJECT": {"initial_life": 55.0, "half_life_days": 180.0, "importance": 0.7},
            "TEMPORARY": {"initial_life": 20.0, "half_life_days": 7.0, "importance": 0.15},
            "IDENTITY": {"initial_life": 60.0, "half_life_days": 730.0, "importance": 0.9},
            "EVENT": {"initial_life": 25.0, "half_life_days": 14.0, "importance": 0.15},
            "PLAN": {"initial_life": 45.0, "half_life_days": 60.0, "importance": 0.6},
            "TEMPORARY_STATE": {"initial_life": 25.0, "half_life_days": 7.0, "importance": 0.2},
            "INFERENCE": {"initial_life": 25.0, "half_life_days": 21.0, "importance": 0.35},
            "FACT": {"initial_life": 35.0, "half_life_days": 60.0, "importance": 0.5},
        }
    )

    decay_interval_days: dict[str, float] = field(
        default_factory=lambda: {
            "SEMANTIC": 7.0,
            "RELATIONSHIP": 7.0,
            "PREFERENCE": 1.0,
            "EPISODIC": 1.0,
            "BEHAVIORAL": 1.0,
            "PROJECT": 1.0,
            "TEMPORARY": 0.25,
            "IDENTITY": 7.0,
            "EVENT": 1.0,
            "PLAN": 1.0,
            "TEMPORARY_STATE": 0.25,
            "INFERENCE": 1.0,
            "FACT": 1.0,
        }
    )

    storage_factors: dict[str, float] = field(
        default_factory=lambda: {"ACTIVE": 1.0, "DORMANT": 0.3, "ARCHIVE": 0.1}
    )

    gc_protected_types: tuple[str, ...] = ("IDENTITY", "RELATIONSHIP")
    gc_max_life: float = 5.0
    gc_importance_threshold: float = 0.2
    gc_min_age_days: int = 365
    gc_unused_days: int = 180
    gc_unconfirmed_days: int = 180

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "MemoryConfig":
        config = cls()
        if not raw:
            return config
        for key in cls.__dataclass_fields__:
            if key not in raw:
                continue
            value = raw[key]
            current = getattr(config, key)
            if isinstance(current, dict) and isinstance(value, dict):
                merged = dict(current)
                merged.update(value)
                setattr(config, key, merged)
            else:
                setattr(config, key, value)
        return config


_PROJECT_KEYWORDS = ("项目", "project", "阶段", "stage", "开发", "版本", "test_mode", "memory", "chris")
_PREFERENCE_SIGNALS = ("喜欢", "偏好", "prefer", "不喜欢", "讨厌", "最爱", "习惯")
_RELATIONSHIP_SIGNALS = ("我是你的", "你是我的", "创造者", "主人", "老板", "我们", "关系", "记得我")
_REMEMBER_STRONG = ("以后记住", "一定要记住", "永远记住", "绝对不要忘", "忘掉这件事", "忘掉")


def _normalize(text: str) -> str:
    import re

    lowered = text.casefold()
    return re.sub(r"[\s，。,.!！？?、；;：:'\"“”‘’()（）\-\t\n]+", "", lowered)


def _token_overlap(a: str, b: str) -> float:
    """Simple token-overlap similarity in [0,1] (no vector db)."""
    na, nb = _normalize(a), _normalize(b)
    if not na or not nb:
        return 0.0
    la, lb = set(na), set(nb)
    union = la | lb
    if not union:
        return 0.0
    return len(la & lb) / len(union)


class MemoryAdmissionPolicy:
    """Deterministic admission rules: what deserves to be a Memory.

    Outputs an action (STORE / IGNORE / MERGE) plus classification
    (type, importance, confidence). No LLM is involved.
    """
    def evaluate(
        self, text: str, explicit_remember: bool = False, source: str = ""
    ) -> tuple[str, str, float]:
        stripped = text.strip()
        if not stripped:
            return ("IGNORE", MemoryType.SEMANTIC.value, 0.0)
        if explicit_remember:
            if any(word in stripped for word in ("对象", "关系", "我是", "你是我的", "老板", "主人", "创造者")):
                return ("STORE", MemoryType.RELATIONSHIP.value, 0.95)
            return ("STORE", MemoryType.PREFERENCE.value, 0.9)
        if any(word in stripped for word in _REMEMBER_STRONG):
            return ("STORE", MemoryType.PREFERENCE.value, 0.9)
        # project information
        if any(k in stripped.casefold() for k in ("chris", "项目", "stage", "阶段", "开发")):
            return ("STORE", MemoryType.PROJECT.value, 0.7)
        # relationship signals
        if any(k in stripped for k in _RELATIONSHIP_SIGNALS):
            return ("STORE", MemoryType.RELATIONSHIP.value, 0.8)
        # preference signals
        if any(k in stripped for k in _PREFERENCE_SIGNALS):
            return ("STORE", MemoryType.PREFERENCE.value, 0.7)
        # short / trivial chatter -> keep as low-value episodic, not long-term
        if len(stripped) <= 12:
            return ("IGNORE", MemoryType.EPISODIC.value, 0.1)
        return ("STORE", MemoryType.EPISODIC.value, 0.3)


class MemoryDecayPolicy:
    """Type-aware, lazy memory decay.

    Decay is NEVER applied by looping every second. Strength is recomputed from
    real timestamps only when the memory is accessed, retrieved, maintained or
    archived. Half-lives come from config (`memory.decay`), not hardcoded.
    """

    def __init__(self, half_life_days: dict[str, float]) -> None:
        self.half_life_days = half_life_days

    @classmethod
    def from_config(cls, config: MemoryConfig) -> "MemoryDecayPolicy":
        merged = {t: p["half_life_days"] for t, p in config.type_params.items()}
        for memory_type, days in (config.decay or {}).items():
            merged[memory_type] = days
        return cls(merged)

    def half_life(self, memory_type: str) -> float:
        return self.half_life_days.get(memory_type, 60.0)

    def decay_factor(self, elapsed_seconds: float, half_life_days: float) -> float:
        seconds = max(half_life_days, 1e-6) * 86400.0
        return 0.5 ** (max(elapsed_seconds, 0.0) / seconds)

    def effective_life(
        self, entry: MemoryEntry, now: datetime, remember_override: float | None = None
    ) -> float:
        """Lazy: current life_score after decay since last_decay_at (no mutation)."""
        base = entry.last_decay_at or entry.timestamp
        elapsed = max(0.0, (now - base).total_seconds())
        half_life = remember_override if remember_override else self.half_life(entry.memory_type)
        return max(0.0, entry.life_score * self.decay_factor(elapsed, half_life))


class MemoryOperation(str, Enum):
    CREATE = "CREATE"
    ACCESS = "ACCESS"
    REINFORCE = "REINFORCE"
    UPDATE = "UPDATE"
    MERGE = "MERGE"
    DECAY = "DECAY"
    ARCHIVE = "ARCHIVE"
    REACTIVATE = "REACTIVATE"


@dataclass
class MemoryOperationProposal:
    """A proposed memory operation (e.g. from the LLM or another module).

    The LLM may only PROPOSE; the memory layer validates and executes.
    """

    operation: str
    content: str = ""
    memory_id: str = ""
    memory_type: str = ""
    remember: bool = False
    source: str = ""
    confidence: float = 0.7
    reason: str = ""
    status: str = "PENDING"  # PENDING / APPROVED / REJECTED


@dataclass
class ProposalDecision:
    approved: bool
    reason: str = ""


class MemoryPolicy:
    """Gates memory operations before they reach the store."""

    def __init__(self, admission: MemoryAdmissionPolicy | None = None) -> None:
        self._admission = admission or MemoryAdmissionPolicy()

    def evaluate(self, proposal: MemoryOperationProposal) -> ProposalDecision:
        op = proposal.operation.upper()
        if op == MemoryOperation.CREATE.value:
            if not proposal.content.strip():
                return ProposalDecision(False, "empty content")
            if proposal.remember:
                return ProposalDecision(True, "explicit user remember")
            action, _mtype, _imp = self._admission.evaluate(
                proposal.content, explicit_remember=False, source=proposal.source
            )
            if action == "IGNORE":
                return ProposalDecision(False, "admission: ignore")
            return ProposalDecision(True, "admission: store")
        if op in (
            MemoryOperation.ACCESS.value,
            MemoryOperation.REINFORCE.value,
            MemoryOperation.DECAY.value,
            MemoryOperation.ARCHIVE.value,
            MemoryOperation.REACTIVATE.value,
            MemoryOperation.UPDATE.value,
            MemoryOperation.MERGE.value,
        ):
            return ProposalDecision(True, f"operation {op}")
        return ProposalDecision(False, f"unknown operation {op}")


@dataclass
class PatternCandidate:
    """Pre-allocated interface for World -> Behavioral Pattern (future).

    Not wired to any learning; only the interface exists this phase.
    """

    pattern: str
    confidence: float = 0.5
    examples: list[str] = field(default_factory=list)

    def to_proposal(self) -> MemoryOperationProposal:
        return MemoryOperationProposal(
            operation=MemoryOperation.CREATE.value,
            content=self.pattern,
            memory_type=MemoryType.BEHAVIORAL.value,
            source="world_observation",
            confidence=self.confidence,
        )


class MemoryEcology(MemorySystem, MemoryRepository):
    """Layered memory store with lifecycle, decay, archive, compression and GC."""

    def __init__(
        self,
        path: Path,
        event_bus: EventBus | None = None,
        temporal: TemporalSystem | None = None,
        config: MemoryConfig | None = None,
        archive_path: Path | None = None,
    ) -> None:
        self.path = path
        self.archive_path = archive_path or path.with_name(path.stem + ".archive.json")
        self._bus = event_bus
        self._temporal = temporal or TemporalSystem()
        self._config = config or MemoryConfig()
        self._entries: dict[str, MemoryEntry] = {}
        self._archived: dict[str, MemoryEntry] = {}
        self._timeline: dict[str, list[dict]] = {}
        self._admission = MemoryAdmissionPolicy()
        self._policy = MemoryPolicy(self._admission)
        self.decay_policy = MemoryDecayPolicy.from_config(self._config)
        self._maintenance: dict[str, Any] = {
            "last_maintenance_at": None,
            "next_maintenance_at": None,
            "maintenance_count": 0,
        }
        self._load()
        self._migrate_and_backup()

    # -- storage --------------------------------------------------------

    def _load(self) -> None:
        self._entries = self._read(self.path)
        self._archived = self._read(self.archive_path)
        self._load_maintenance_metadata()

    @staticmethod
    def _read(path: Path) -> dict[str, MemoryEntry]:
        if not path.exists():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, OSError):
            return {}
        items = raw.get("entries") if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            return {}
        return {
            item["memory_id"]: MemoryEntry.from_dict(item)
            for item in items
            if isinstance(item, dict) and item.get("memory_id")
        }

    def _load_maintenance_metadata(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, OSError):
            return
        meta = raw.get("maintenance") if isinstance(raw, dict) else None
        if isinstance(meta, dict):
            self._maintenance.update({k: meta[k] for k in self._maintenance if k in meta})

    def _migrate_and_backup(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, OSError):
            return
        version = raw.get("schema_version", 1) if isinstance(raw, dict) else 1
        if version < self._config.schema_version:
            backups = self.path.parent / "backups"
            backups.mkdir(parents=True, exist_ok=True)
            stamp = self._temporal.now().strftime("%Y%m%d-%H%M%S")
            shutil.copy2(self.path, backups / f"{self.path.stem}-v{version}-{stamp}.json")

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._write(self.path, self._entries, self._maintenance)
        self._write(self.archive_path, self._archived)

    @staticmethod
    def _write(path: Path, entries: dict[str, MemoryEntry], maintenance: dict | None = None) -> None:
        data: dict[str, Any] = {
            "schema_version": MemoryConfig().schema_version,
            "entries": [
                e.to_dict()
                for e in sorted(entries.values(), key=lambda e: (e.timestamp, e.memory_id))
            ],
        }
        if maintenance is not None:
            data["maintenance"] = dict(maintenance)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # -- interface: MemorySystem ---------------------------------------

    def add(self, entry: MemoryEntry, remember: bool | None = None) -> MemoryEntry:
        now = self._temporal.now()
        remember = (
            remember if remember is not None else bool(entry.metadata.get("remember"))
        )
        if not remember and not entry.metadata.get("skip_dedup"):
            duplicate = self._find_similar(entry.content)
            if duplicate is not None:
                duplicate = self.reinforce(duplicate.memory_id, reason="merged duplicate")
                duplicate.access_count += 1
                duplicate.last_accessed_at = now
                self._emit(EventType.MEMORY_UPDATED, duplicate, reason="dedup merged")
                self._save()
                return duplicate
        entry.timestamp = now
        if not entry.memory_type:
            if remember or entry.kind in ("fact", "note"):
                _action, classified, _imp = self._admission.evaluate(
                    entry.content, explicit_remember=remember, source=entry.source
                )
                entry.memory_type = classified
            else:
                entry.memory_type = kind_to_type(entry.kind)
        entry.memory_type = entry.memory_type or kind_to_type(entry.kind)
        entry.source = entry.source or _default_source(entry)
        params = self._type_params(entry.memory_type)
        entry.life_score = params["initial_life"]
        if entry.importance == 0.5:
            entry.importance = params["importance"]
        entry.confidence = self._config.source_weights.get(entry.source, 0.7)
        if remember:
            entry.source = MemorySource.EXPLICIT_USER.value
            entry.importance = self._config.remember_importance
            entry.confidence = max(
                entry.confidence, self._config.source_weights[MemorySource.EXPLICIT_USER.value]
            )
            entry.life_score = max(entry.life_score, self._config.remember_min_life)
        entry.status = MemoryStatus.ACTIVE.value
        entry.storage_tier = StorageTier.ACTIVE.value
        entry.last_confirmed_at = now
        entry.last_used_at = now
        entry.last_accessed_at = now
        entry.last_decay_at = now
        entry.next_decay_at = now + self._decay_interval(entry.memory_type)
        entry.version = 1
        if not entry.summary:
            entry.summary = entry.content[:60]
        self._entries[entry.memory_id] = entry
        self._enforce_active_limit()
        self._save()
        self._emit(EventType.MEMORY_CREATED, entry, reason="created")
        return entry

    def create(self, entry: MemoryEntry) -> MemoryEntry:
        """Repository contract: alias of add()."""
        return self.add(entry)

    def get(self, memory_id: str) -> MemoryEntry | None:
        return self._entries.get(memory_id) or self._archived.get(memory_id)

    def delete(self, memory_id: str) -> bool:
        removed = self._entries.pop(memory_id, None) or self._archived.pop(memory_id, None)
        if removed is not None:
            self._save()
            return True
        return False

    def list_recent(self, limit: int = 10, include_dormant: bool = False) -> list[MemoryEntry]:
        entries = [
            e
            for e in self._entries.values()
            if e.status != MemoryStatus.FORGOTTEN.value
            and (e.storage_tier == StorageTier.ACTIVE.value or include_dormant)
        ]
        ordered = sorted(entries, key=lambda e: (e.timestamp, e.memory_id), reverse=True)
        return ordered[:limit]

    def search(self, query: str, limit: int = 5) -> list[MemoryEntry]:
        results = self.query(text=query, limit=limit)
        return [r.memory for r in results]

    # -- retrieval ranking (Memory 2.0) --------------------------------

    def query(
        self,
        query_obj: MemoryQuery | None = None,
        text: str = "",
        limit: int | None = None,
    ) -> list[MemoryResult]:
        q = query_obj or MemoryQuery(text=text, limit=limit or 5)
        needle = q.text.casefold()
        now = self._temporal.now()
        results: list[MemoryResult] = []
        for entry in list(self._entries.values()) + list(self._archived.values()):
            if entry.status == MemoryStatus.FORGOTTEN.value:
                continue
            if entry.storage_tier == StorageTier.ARCHIVE.value and not q.include_archived:
                continue
            relevance = self._keyword_relevance(entry, needle)
            if relevance <= 0:
                continue
            # lazy decay: rank with current (decayed) strength, no per-second writes
            effective_life = self.decay_policy.effective_life(
                entry, now, self._config.remember_half_life_days if entry.metadata.get("remember") else None
            )
            score, reason = self._rank_with_life(entry, relevance, effective_life)
            results.append(MemoryResult(memory=entry, score=score, reason=reason))
        results.sort(key=lambda r: r.score, reverse=True)
        top = results[: q.limit]
        if top:
            # retrieval counts as access: update counts lazily and persist once
            changed = False
            for result in top:
                entry = result.memory
                entry.access_count += 1
                entry.last_accessed_at = now
                changed = True
            if changed:
                self._save()
        return top

    def _rank_with_life(
        self, entry: MemoryEntry, relevance: float, effective_life: float
    ) -> tuple[float, dict[str, float]]:
        vitality = max(0.0, min(1.0, effective_life / 100.0))
        now = self._temporal.now()
        last_used = entry.last_used_at or entry.timestamp
        age_days = max(0.0, (now - last_used).total_seconds() / 86400.0)
        recency = 1.0 / (1.0 + age_days / 30.0)
        storage = self._config.storage_factors.get(entry.storage_tier, 0.1)
        score = (
            relevance
            * (0.5 + 0.5 * vitality)
            * (0.4 + 0.6 * entry.importance)
            * (0.4 + 0.6 * entry.confidence)
            * (0.3 + 0.7 * recency)
            * storage
        )
        reason = {
            "relevance": relevance,
            "importance": entry.importance,
            "vitality": vitality,
            "confidence": entry.confidence,
            "recency": recency,
            "storage": storage,
        }
        return score, reason

    @staticmethod
    def _keyword_relevance(entry: MemoryEntry, needle: str) -> float:
        if not needle:
            return 0.0
        haystack = (entry.content + " " + (entry.summary or "")).casefold()
        if needle in haystack:
            return 1.0
        for tag in list(entry.tags) + list(entry.metadata.get("tags", [])):
            if needle in str(tag).casefold():
                return 0.9
        # char-overlap fallback for paraphrased queries (lightweight, no vector db)
        query_chars = set(_normalize(needle))
        content_chars = set(_normalize(entry.content))
        if not query_chars:
            return 0.0
        overlap = len(query_chars & content_chars) / len(query_chars)
        if overlap >= 0.4:
            return 0.5 + 0.5 * overlap
        return 0.0

    def _rank(self, entry: MemoryEntry, relevance: float) -> tuple[float, dict[str, float]]:
        vitality = entry.vitality
        now = self._temporal.now()
        last_used = entry.last_used_at or entry.timestamp
        age_days = max(0.0, (now - last_used).total_seconds() / 86400.0)
        recency = 1.0 / (1.0 + age_days / 30.0)
        storage = self._config.storage_factors.get(entry.storage_tier, 0.1)
        score = (
            relevance
            * (0.5 + 0.5 * vitality)
            * (0.4 + 0.6 * entry.importance)
            * (0.4 + 0.6 * entry.confidence)
            * (0.3 + 0.7 * recency)
            * storage
        )
        reason = {
            "relevance": relevance,
            "importance": entry.importance,
            "vitality": vitality,
            "confidence": entry.confidence,
            "recency": recency,
            "storage": storage,
        }
        return score, reason

    def _find_similar(self, content: str) -> MemoryEntry | None:
        if len(_normalize(content)) > self._config.dedup_max_len:
            return None
        best: MemoryEntry | None = None
        best_score = 0.0
        for entry in self._entries.values():
            if entry.status == MemoryStatus.FORGOTTEN.value:
                continue
            if entry.storage_tier != StorageTier.ACTIVE.value:
                continue
            if len(_normalize(entry.content)) > self._config.dedup_max_len:
                continue
            score = _token_overlap(content, entry.content)
            if score > best_score:
                best, best_score = entry, score
        if best is not None and best_score >= self._config.dedup_similarity:
            return best
        return None

    # -- lifecycle ------------------------------------------------------

    def reinforce(
        self, memory_id: str, boost: float | None = None, reason: str = ""
    ) -> MemoryEntry | None:
        entry = self.get(memory_id)
        if entry is None or entry.status == MemoryStatus.FORGOTTEN.value:
            return None
        now = self._temporal.now()
        boost = boost or self._config.reinforce_boost
        effective = boost * (self._config.reinforce_diminish ** min(entry.reinforcement_count, 5))
        entry.life_score = min(entry.life_score + effective, self._config.reinforce_cap)
        entry.reinforcement_count += 1
        entry.last_confirmed_at = now
        entry.last_used_at = now
        entry.last_decay_at = now  # reinforcement resets the decay baseline
        entry.next_decay_at = now + self._decay_interval(entry.memory_type)
        if entry.storage_tier in (StorageTier.DORMANT.value, StorageTier.ARCHIVE.value):
            self._recover(entry)
            self._emit(EventType.MEMORY_RECOVERED, entry, reason=reason or "re-inforced while dormant/archived")
        else:
            entry.status = MemoryStatus.REINFORCED.value
            self._emit(EventType.MEMORY_REINFORCED, entry, reason=reason or "reinforced")
        self._save()
        return entry

    def access(self, memory_id: str) -> MemoryEntry | None:
        """Retrieval: apply lazy decay, then count the access.

        Access != Reinforcement: this never boosts strength. Only updates
        access_count / last_accessed_at and lets decay run.
        """
        entry = self.get(memory_id)
        if entry is None or entry.status == MemoryStatus.FORGOTTEN.value:
            return None
        now = self._temporal.now()
        self._apply_lazy_decay(entry, now)
        entry.access_count += 1
        entry.last_accessed_at = now
        entry.last_used_at = now
        self._emit(EventType.MEMORY_ACCESSED, entry, reason="accessed")
        self._save()
        return entry

    def touch(self, memory_id: str) -> MemoryEntry | None:
        return self.access(memory_id)

    def propose(self, proposal: MemoryOperationProposal) -> MemoryEntry | None:
        """Unified entry for gated memory mutations (LLM/other modules propose)."""
        decision = self._policy.evaluate(proposal)
        if not decision.approved:
            proposal.status = "REJECTED"
            return None
        proposal.status = "APPROVED"
        op = proposal.operation.upper()
        if op == MemoryOperation.CREATE.value:
            return self.add(
                MemoryEntry(
                    kind="fact",
                    content=proposal.content,
                    memory_type=proposal.memory_type or "",
                    source=proposal.source or "",
                    confidence=proposal.confidence,
                    metadata={"remember": True} if proposal.remember else {},
                ),
                remember=proposal.remember,
            )
        if op == MemoryOperation.REINFORCE.value:
            return self.reinforce(proposal.memory_id, reason=proposal.reason)
        if op == MemoryOperation.DECAY.value:
            self.decay(proposal.memory_id or None)
            return self.get(proposal.memory_id) if proposal.memory_id else None
        if op == MemoryOperation.ARCHIVE.value:
            return self.archive_one(proposal.memory_id)
        if op == MemoryOperation.REACTIVATE.value:
            return self.restore(proposal.memory_id)
        if op == MemoryOperation.ACCESS.value:
            return self.access(proposal.memory_id)
        return None

    def forget(self, memory_id: str) -> MemoryEntry | None:
        """User requested forgetting: FORGOTTEN, excluded from retrieval, not deleted."""
        entry = self.get(memory_id)
        if entry is None:
            return None
        entry.status = MemoryStatus.FORGOTTEN.value
        self._emit(EventType.MEMORY_FORGOTTEN, entry, reason="user requested forget")
        self._save()
        return entry

    def restore(self, memory_id: str) -> MemoryEntry | None:
        entry = self.get(memory_id)
        if entry is None:
            return None
        entry.status = MemoryStatus.ACTIVE.value
        entry.storage_tier = StorageTier.ACTIVE.value
        self._emit(EventType.MEMORY_RESTORED, entry, reason="restored")
        self._save()
        return entry

    def update(self, entry: MemoryEntry) -> MemoryEntry:
        store = self._entries if entry.memory_id in self._entries else self._archived
        store[entry.memory_id] = entry
        self._save()
        return entry

    def list_all(self) -> list[MemoryEntry]:
        return list(self._entries.values()) + list(self._archived.values())

    def timeline(self, memory_id: str) -> list[dict]:
        return list(self._timeline.get(memory_id, []))

    def decay(self, memory_id: str | None = None) -> list[str]:
        now = self._temporal.now()
        if memory_id is not None:
            entry = self._entries.get(memory_id)
            if entry is None:
                return []
            changed = [memory_id] if self._decay_one(entry, now) else []
        else:
            changed = [mid for mid in self._due_ids(now) if self._decay_one(self._entries[mid], now)]
        for mid in list(changed):
            entry = self._entries.get(mid)
            if entry is not None:
                self._maybe_archive(entry, now)
        self._save()
        return changed

    def invalidate(self, memory_id: str, reason: str = "") -> MemoryEntry | None:
        entry = self.get(memory_id)
        if entry is None:
            return None
        entry.status = MemoryStatus.INVALIDATED.value
        entry.contradiction_count += 1
        self._emit(EventType.MEMORY_INVALIDATED, entry, reason=reason or "invalidated")
        self._save()
        return entry

    def archive_one(self, memory_id: str, reason: str = "") -> MemoryEntry | None:
        entry = self._entries.get(memory_id)
        if entry is None:
            return None
        entry.status = MemoryStatus.ARCHIVED.value
        entry.storage_tier = StorageTier.ARCHIVE.value
        self._archived[entry.memory_id] = entry
        self._entries.pop(entry.memory_id, None)
        self._emit(EventType.MEMORY_ARCHIVED, entry, reason=reason or "archived")
        self._save()
        return entry

    def supersede(self, old_id: str, new_entry: MemoryEntry) -> MemoryEntry | None:
        old = self.get(old_id)
        if old is None:
            return None
        old.status = MemoryStatus.SUPERSEDED.value
        old.contradiction_count += 1
        old.superseded_by = None
        new = self.add(new_entry)
        new.supersedes = old.memory_id
        new.contradiction_count = old.contradiction_count + 1
        new.life_score = min(new.life_score + self._config.reinforce_boost, self._config.reinforce_cap)
        old.superseded_by = new.memory_id
        self._emit(EventType.MEMORY_CONTRADICTED, old, reason="contradicted by new memory")
        self._emit(EventType.MEMORY_SUPERSEDED, old, reason=f"superseded by {new.memory_id}")
        self._save()
        return new

    def process_due(self) -> list[str]:
        changed = self.decay()
        return changed

    def maintenance(self) -> dict[str, Any]:
        """Idempotent, fault-isolated maintenance service.

        Only processes candidate memories (due for decay, or already
        WEAKENING/DORMANT) - no unconditional full scan-and-rewrite. Applies
        lazy decay via MemoryDecayPolicy, runs vitality-driven state transition,
        archives low-strength memories and normalizes metadata. One corrupt
        memory never blocks the rest.
        """
        started = self._temporal.now()
        processed = decayed = archived = normalized = errors = 0
        for mid in self._maintenance_candidates(started):
            entry = self._entries.get(mid)
            if entry is None or entry.status == MemoryStatus.FORGOTTEN.value:
                continue
            try:
                old_life = entry.life_score
                new_life = self._apply_lazy_decay(entry, started)
                if new_life < old_life - 1e-9:
                    decayed += 1
                before_tier = entry.storage_tier
                self._maybe_archive(entry, started)
                if before_tier != entry.storage_tier:
                    archived += 1
                if self._normalize_entry(entry):
                    normalized += 1
                processed += 1
            except Exception:  # noqa: BLE001
                errors += 1
        self._save()
        duration = (self._temporal.now() - started).total_seconds()
        return {
            "processed": processed,
            "decayed": decayed,
            "archived": archived,
            "reactivated": 0,
            "normalized": normalized,
            "errors": errors,
            "duration_seconds": round(duration, 4),
            "interval_seconds": self._config.maintenance_interval_seconds,
            "last_maintenance_at": started.isoformat(),
        }

    def _maintenance_candidates(self, now: datetime) -> list[str]:
        candidates = set(self._due_ids(now))
        for mid, entry in self._entries.items():
            if entry.status in (MemoryStatus.WEAKENING.value, MemoryStatus.DORMANT.value):
                candidates.add(mid)
        return list(candidates)

    def _normalize_entry(self, entry: MemoryEntry) -> bool:
        changed = False
        if not (0.0 <= entry.life_score <= 100.0):
            entry.life_score = max(0.0, min(100.0, entry.life_score))
            changed = True
        if not (0.0 <= entry.importance <= 1.0):
            entry.importance = max(0.0, min(1.0, entry.importance))
            changed = True
        if not (0.0 <= entry.confidence <= 1.0):
            entry.confidence = max(0.0, min(1.0, entry.confidence))
            changed = True
        try:
            MemoryStatus(entry.status)
        except ValueError:
            entry.status = MemoryStatus.ACTIVE.value
            changed = True
        if entry.memory_type:
            try:
                MemoryType(entry.memory_type)
            except ValueError:
                entry.memory_type = MemoryType.SEMANTIC.value
                changed = True
        return changed

    def record_maintenance(self, now: datetime) -> dict[str, Any]:
        self._maintenance["last_maintenance_at"] = now.isoformat()
        self._maintenance["next_maintenance_at"] = (
            now + timedelta(seconds=self._config.maintenance_interval_seconds)
        ).isoformat()
        self._maintenance["maintenance_count"] = int(self._maintenance.get("maintenance_count", 0)) + 1
        self._save()
        return dict(self._maintenance)

    def maintenance_metadata(self) -> dict[str, Any]:
        return dict(self._maintenance)

    # -- compression ----------------------------------------------------

    def compress(self, topic: str | None = None) -> list[MemoryEntry]:
        created: list[MemoryEntry] = []
        groups: dict[str, list[MemoryEntry]] = {}
        for entry in self._archived.values():
            key = topic or entry.metadata.get("topic") or entry.memory_type
            if not key:
                continue
            groups.setdefault(key, []).append(entry)
        for key, entries in groups.items():
            if len(entries) < 2:
                continue
            new_entry = self._make_compressed(key, entries)
            self._archived[new_entry.memory_id] = new_entry
            for entry in entries:
                entry.metadata["compressed_into"] = new_entry.memory_id
            created.append(new_entry)
            self._emit(EventType.MEMORY_COMPRESSED, new_entry, reason=f"compressed {len(entries)} memories")
        self._save()
        return created

    def _make_compressed(self, topic: str, entries: list[MemoryEntry]) -> MemoryEntry:
        now = self._temporal.now()
        first = min(e.timestamp for e in entries)
        last = max(e.timestamp for e in entries)
        content = (
            f"[{topic}] {len(entries)} 条相关记忆压缩，覆盖 {first:%Y-%m-%d} ~ {last:%Y-%m-%d}。"
            f" 代表内容：{'；'.join(e.content[:40] for e in entries[:3])}"
        )
        new_entry = MemoryEntry(
            kind="fact",
            content=content,
            memory_type=MemoryType.FACT.value,
            source=MemorySource.SYSTEM_INFERENCE.value,
            timestamp=now,
            last_confirmed_at=now,
            last_used_at=now,
            life_score=self._config.min_life_score,
            importance=max(e.importance for e in entries),
            confidence=min(e.confidence for e in entries),
            status=MemoryStatus.ARCHIVED.value,
            storage_tier=StorageTier.ARCHIVE.value,
            compressed_from=[e.memory_id for e in entries],
            historical_value=0.5,
            metadata={"topic": topic, "compressed": True},
        )
        return new_entry

    # -- garbage collection ---------------------------------------------

    def gc(self) -> list[MemoryEntry]:
        removed: list[MemoryEntry] = []
        if not self._config.gc_enabled:
            return removed
        now = self._temporal.now()
        all_referenced = self._referenced_ids()
        for store in (self._entries, self._archived):
            for memory_id in list(store):
                entry = store[memory_id]
                if self._gc_eligible(entry, now, all_referenced):
                    removed.append(entry)
                    del store[memory_id]
                    self._emit(
                        EventType.MEMORY_GARBAGE_COLLECTED,
                        entry,
                        reason="gc: low life + low importance + long unused/unconfirmed + unreferenced",
                    )
        self._save()
        return removed

    def _gc_eligible(self, entry: MemoryEntry, now: datetime, referenced: set[str]) -> bool:
        if not self._config.gc_enabled:
            return False
        if entry.memory_type in self._config.gc_protected_types:
            return False
        if entry.importance > self._config.gc_importance_threshold:
            return False
        if entry.life_score > self._config.gc_max_life:
            return False
        if entry.memory_id in referenced:
            return False
        age = now - entry.timestamp
        unused = now - (entry.last_used_at or entry.timestamp)
        unconfirmed = now - (entry.last_confirmed_at or entry.timestamp)
        if age.days < self._config.gc_min_age_days:
            return False
        if unused.days < self._config.gc_unused_days:
            return False
        if unconfirmed.days < self._config.gc_unconfirmed_days:
            return False
        return True

    def _referenced_ids(self) -> set[str]:
        ids: set[str] = set()
        for entry in list(self._entries.values()) + list(self._archived.values()):
            ids.update(entry.compressed_from)
        return ids

    # -- queries --------------------------------------------------------

    def list_active(self, limit: int = 50) -> list[MemoryEntry]:
        return self.list_recent(limit=limit)

    def list_remembered(self, limit: int = 5) -> list[MemoryEntry]:
        """Explicit 'remember' memories the user asked Chris to keep.

        Used to surface these reliably in the prompt regardless of conversation
        volume. Dormant/archived/forgotten ones are excluded.
        """
        entries = [
            e
            for e in self._entries.values()
            if e.status != MemoryStatus.FORGOTTEN.value
            and e.storage_tier == StorageTier.ACTIVE.value
            and e.metadata.get("remember")
        ]
        ordered = sorted(entries, key=lambda e: (e.timestamp, e.memory_id), reverse=True)
        return ordered[:limit]

    def list_dormant(self, limit: int = 50) -> list[MemoryEntry]:
        entries = [
            e
            for e in self._entries.values()
            if e.status != MemoryStatus.FORGOTTEN.value
            and e.storage_tier == StorageTier.DORMANT.value
        ]
        return sorted(entries, key=lambda e: (e.timestamp, e.memory_id), reverse=True)[:limit]

    def list_archived(self, limit: int = 50) -> list[MemoryEntry]:
        entries = [e for e in self._archived.values() if e.status != MemoryStatus.FORGOTTEN.value]
        return sorted(entries, key=lambda e: (e.timestamp, e.memory_id), reverse=True)[:limit]

    @staticmethod
    def _vitality_band(entry: MemoryEntry) -> str:
        v = entry.vitality
        if v >= 0.6:
            return "ACTIVE"
        if v >= 0.3:
            return "WEAKENING"
        if v >= 0.1:
            return "DORMANT"
        return "ARCHIVED"

    def stats(self) -> dict[str, Any]:
        all_entries = list(self._entries.values()) + list(self._archived.values())
        status_counts = Counter(e.status for e in all_entries)
        tier_counts = Counter(e.storage_tier for e in all_entries)
        type_counts = Counter(e.memory_type or "UNKNOWN" for e in all_entries)
        vitality_counts = Counter(self._vitality_band(e) for e in all_entries)
        compressed = sum(1 for e in all_entries if e.compressed_from)
        total_bytes = sum(len(json.dumps(e.to_dict(), ensure_ascii=False)) for e in all_entries)
        average_strength = (sum(e.strength for e in all_entries) / len(all_entries)) if all_entries else 0.0
        return {
            "total": len(all_entries),
            "active": tier_counts.get("ACTIVE", 0),
            "dormant": tier_counts.get("DORMANT", 0),
            "archived": tier_counts.get("ARCHIVE", 0),
            "weakening": status_counts.get("WEAKENING", 0),
            "average_strength": round(average_strength, 4),
            "compressed": compressed,
            "status": dict(status_counts),
            "vitality": dict(vitality_counts),
            "types": dict(type_counts),
            "estimated_storage_bytes": total_bytes,
            "schema_version": self._config.schema_version,
            "last_maintenance_at": self._maintenance.get("last_maintenance_at"),
            "next_maintenance_at": self._maintenance.get("next_maintenance_at"),
            "maintenance_count": int(self._maintenance.get("maintenance_count", 0)),
        }

    # -- internals ------------------------------------------------------

    def _type_params(self, memory_type: str) -> dict[str, float]:
        return self._config.type_params.get(memory_type, self._config.type_params["FACT"])

    def _decay_interval(self, memory_type: str) -> timedelta:
        days = self._config.decay_interval_days.get(memory_type, 1.0)
        return timedelta(days=days)

    def _due_ids(self, now: datetime) -> list[str]:
        return [
            mid
            for mid, entry in self._entries.items()
            if entry.next_decay_at is not None and entry.next_decay_at <= now
        ]

    def _apply_lazy_decay(self, entry: MemoryEntry, now: datetime) -> float:
        """Lazy decay: recompute life from timestamps, mutate, and run the
        vitality-driven status transition. Used by access / retrieval /
        maintenance. Remember memories decay slower (no floor, not eternal).
        """
        remember_override = (
            self._config.remember_half_life_days if entry.metadata.get("remember") else None
        )
        new_life = self.decay_policy.effective_life(entry, now, remember_override)
        new_life = max(new_life, self._config.min_life_score)
        changed = new_life < entry.life_score - 1e-9
        previous = entry.to_dict() if changed else None
        entry.life_score = new_life
        entry.last_decay_at = now
        entry.next_decay_at = now + self._decay_interval(entry.memory_type)
        if entry.storage_tier == StorageTier.ACTIVE.value:
            if new_life < self._config.dormant_life_threshold:
                entry.status = MemoryStatus.DORMANT.value
                entry.storage_tier = StorageTier.DORMANT.value
                if changed:
                    self._emit(EventType.MEMORY_DORMANT, entry, reason="life below dormant threshold", previous=previous)
            elif new_life < self._config.stable_life_threshold:
                entry.status = MemoryStatus.WEAKENING.value
            else:
                entry.status = MemoryStatus.STABLE.value
        return new_life

    def _decay_one(self, entry: MemoryEntry, now: datetime) -> bool:
        if entry.next_decay_at is not None and entry.next_decay_at > now:
            return False
        old_life = entry.life_score
        new_life = self._apply_lazy_decay(entry, now)
        if new_life >= old_life - 1e-9:
            return False
        self._emit(EventType.MEMORY_WEAKENED, entry, reason="decay")
        return True

    def _maybe_archive(self, entry: MemoryEntry, now: datetime) -> None:
        if entry.status != MemoryStatus.DORMANT.value or entry.storage_tier != StorageTier.DORMANT.value:
            return
        inactive = now - (entry.last_used_at or entry.timestamp)
        low_vitality = entry.life_score < self._config.archive_life_threshold
        if inactive.days >= self._config.archive_after_days or low_vitality:
            self._archived[entry.memory_id] = entry
            self._entries.pop(entry.memory_id, None)
            entry.status = MemoryStatus.ARCHIVED.value
            entry.storage_tier = StorageTier.ARCHIVE.value
            self._emit(EventType.MEMORY_ARCHIVED, entry, reason="dormant too long or vitality below threshold")

    def _recover(self, entry: MemoryEntry) -> None:
        self._archived.pop(entry.memory_id, None)
        self._entries[entry.memory_id] = entry
        entry.status = MemoryStatus.RECOVERED.value
        entry.storage_tier = StorageTier.ACTIVE.value
        entry.last_confirmed_at = self._temporal.now()
        entry.last_used_at = self._temporal.now()
        entry.valid_until = None
        entry.next_decay_at = self._temporal.now() + self._decay_interval(entry.memory_type)

    def _enforce_active_limit(self) -> None:
        if len(self._entries) <= self._config.active_limit:
            return
        active = [
            e
            for e in self._entries.values()
            if e.storage_tier == StorageTier.ACTIVE.value
        ]
        active.sort(key=lambda e: (e.life_score, e.importance))
        overflow = len(active) - self._config.active_limit
        for entry in active[:overflow]:
            entry.status = MemoryStatus.DORMANT.value
            entry.storage_tier = StorageTier.DORMANT.value
            self._emit(EventType.MEMORY_DORMANT, entry, reason="active limit reached")

    def _retrieval_score(self, entry: MemoryEntry, relevance: float) -> float:
        life_factor = max(0.0, min(entry.life_score, 100.0)) / 100.0
        now = self._temporal.now()
        last_used = entry.last_used_at or entry.timestamp
        age_days = max(0.0, (now - last_used).total_seconds() / 86400.0)
        recency = 1.0 / (1.0 + age_days / 30.0)
        storage = self._config.storage_factors.get(entry.storage_tier, 0.1)
        return relevance * life_factor * entry.confidence * entry.importance * recency * storage

    def _emit(
        self,
        event_type: EventType,
        entry: MemoryEntry,
        reason: str = "",
        previous: dict[str, Any] | None = None,
    ) -> None:
        payload = {
            "memory_id": entry.memory_id,
            "timestamp": self._temporal.now().isoformat(),
            "reason": reason,
            "current_state": entry.to_dict(),
        }
        if previous is not None:
            payload["previous_state"] = previous
        self._timeline.setdefault(entry.memory_id, []).append(
            {"event": event_type.value, "at": self._temporal.now().isoformat(), "reason": reason}
        )
        self._timeline[entry.memory_id] = self._timeline[entry.memory_id][-50:]
        if self._bus:
            self._bus.publish(Event(event_type, "memory", payload))
