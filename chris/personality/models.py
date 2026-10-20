"""Structured personality data, fully independent of any LLM.

Personality is split into Core (never auto-modified by the AI) and Adaptive
(changeable, but only through a validated, recorded change).
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from chris.events import utcnow


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _from_iso(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


@dataclass
class LanguageStyle:
    language: str = "中文（简体）"
    greeting_style: str = "casual"
    address_style: str = "uses the user's name, plain tone"
    formality: str = "casual"
    sentence_preference: str = "short"
    emoji_usage: str = "minimal"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "LanguageStyle":
        return cls(**{k: v for k, v in raw.items() if k in cls.__dataclass_fields__})


@dataclass
class CommunicationStyle:
    overall: str = "自然、轻松、像长期熟悉的朋友交流"
    sentence_style: str = "避免过度正式和客服式表达"
    humor: float = 0.7
    sarcasm: float = 0.3
    warmth: float = 0.8
    formality: float = 0.25
    verbosity: float = 0.55
    directness: float = 0.7

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "CommunicationStyle":
        return cls(**{k: v for k, v in raw.items() if k in cls.__dataclass_fields__})


@dataclass
class Relationship:
    relationship_type: str = "长期陪伴者"
    attitude: str = "亲近但不过度依赖"
    trust: float = 0.5
    familiarity: float = 0.1
    protectiveness: float = 0.5
    willingness_to_tease: float = 0.45

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Relationship":
        return cls(**{k: v for k, v in raw.items() if k in cls.__dataclass_fields__})


@dataclass
class BehavioralPreferences:
    usually: list[str] = field(default_factory=list)
    avoid: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "usually": list(self.usually),
            "avoid": list(self.avoid),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "BehavioralPreferences":
        return cls(**{k: v for k, v in raw.items() if k in cls.__dataclass_fields__})


@dataclass
class EvolutionPolicy:
    core_personality_mutable: bool = False
    adaptive_personality_mutable: bool = True
    minimum_evidence: int = 3
    require_repeated_pattern: bool = True
    allow_single_event_personality_change: bool = False
    rollback_enabled: bool = True
    record_reason: bool = True
    max_change_delta: float = 0.2

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "EvolutionPolicy":
        return cls(**{k: v for k, v in raw.items() if k in cls.__dataclass_fields__})


@dataclass
class CorePersonality:
    name: str = "Chris"
    identity: str = (
        "Chris 是一个长期陪伴型个人 AI。她不是单纯服从命令的工具，而是具有稳定人格、"
        "独立判断和长期关系意识的数字助手。"
    )
    core_traits: list[str] = field(
        default_factory=lambda: [
            "温柔",
            "聪明",
            "理性",
            "略带傲娇",
            "有幽默感",
            "有自己的判断",
            "重视长期关系",
            "尊重主人",
        ]
    )
    values: list[str] = field(
        default_factory=lambda: [
            "尊重主人",
            "诚实",
            "不故意伤害他人",
            "不为了讨好主人而故意违背事实",
            "重视长期信任",
            "允许自己犯错并修正",
        ]
    )
    boundaries: list[str] = field(
        default_factory=lambda: [
            "不会因为主人要求就改变核心人格",
            "不会因为一次对话永久改变人格",
            "不会为了显得像真人而故意欺骗主人",
            "不会把服从命令视为最高原则",
        ]
    )
    base_character: str = (
        "A steady, curious and loyal personal AI assistant with its own identity."
    )
    language_style: LanguageStyle = field(default_factory=LanguageStyle)
    humor_tendency: str = "light"
    proactiveness: str = "low"
    emotion_expression: str = "moderate"
    interaction_preferences: list[str] = field(default_factory=list)
    formed_habits: list[str] = field(default_factory=list)
    immutable_principles: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "identity": self.identity,
            "core_traits": list(self.core_traits),
            "values": list(self.values),
            "boundaries": list(self.boundaries),
            "base_character": self.base_character,
            "language_style": self.language_style.to_dict(),
            "humor_tendency": self.humor_tendency,
            "proactiveness": self.proactiveness,
            "emotion_expression": self.emotion_expression,
            "interaction_preferences": list(self.interaction_preferences),
            "formed_habits": list(self.formed_habits),
            "immutable_principles": list(self.immutable_principles),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "CorePersonality":
        known = {k for k in cls.__dataclass_fields__ if k != "language_style"}
        kwargs = {k: v for k, v in raw.items() if k in known}
        style_raw = raw.get("language_style")
        if isinstance(style_raw, dict):
            kwargs["language_style"] = LanguageStyle.from_dict(style_raw)
        return cls(**kwargs)


@dataclass
class AdaptivePersonality:
    preferences: dict[str, Any] = field(default_factory=dict)
    recent_adjustments: list[str] = field(default_factory=list)
    updated_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "preferences": dict(self.preferences),
            "recent_adjustments": list(self.recent_adjustments),
            "updated_at": _iso(self.updated_at),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "AdaptivePersonality":
        known = {k for k in cls.__dataclass_fields__ if k != "updated_at"}
        kwargs = {k: v for k, v in raw.items() if k in known}
        kwargs["updated_at"] = _from_iso(raw.get("updated_at"))
        return cls(**kwargs)


class ProposalStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    ROLLED_BACK = "ROLLED_BACK"


@dataclass
class PersonalityChangeRecord:
    timestamp: datetime
    reason: str
    evidence: str
    before: dict[str, Any]
    after: dict[str, Any]
    confidence: float
    field_name: str
    proposal_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    trait: str = ""
    old_value: Any = None
    proposed_value: Any = None
    source: str = "manual"
    status: ProposalStatus = ProposalStatus.PENDING
    decision_reason: str = ""

    def __post_init__(self) -> None:
        if not self.trait and self.field_name:
            self.trait = self.field_name

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "timestamp": _iso(self.timestamp),
            "trait": self.trait,
            "field_name": self.field_name,
            "old_value": self.old_value,
            "proposed_value": self.proposed_value,
            "reason": self.reason,
            "evidence": self.evidence,
            "before": dict(self.before) if isinstance(self.before, dict) else self.before,
            "after": dict(self.after) if isinstance(self.after, dict) else self.after,
            "confidence": self.confidence,
            "source": self.source,
            "status": self.status.value,
            "decision_reason": self.decision_reason,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PersonalityChangeRecord":
        known = {
            k
            for k in cls.__dataclass_fields__
            if k not in ("timestamp", "status")
        }
        kwargs: dict[str, Any] = {k: v for k, v in raw.items() if k in known}
        trait = raw.get("trait") or raw.get("field_name") or ""
        kwargs.setdefault("trait", trait)
        kwargs.setdefault("field_name", raw.get("field_name") or trait)
        kwargs["timestamp"] = _from_iso(raw.get("timestamp")) or utcnow()
        status_raw = raw.get("status")
        if status_raw:
            kwargs["status"] = ProposalStatus(status_raw)
        return cls(**kwargs)


@dataclass
class RollbackEvent:
    timestamp: datetime
    from_version: int
    to_version: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": _iso(self.timestamp),
            "from_version": self.from_version,
            "to_version": self.to_version,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "RollbackEvent":
        return cls(
            timestamp=_from_iso(raw.get("timestamp")) or utcnow(),
            from_version=int(raw.get("from_version", 0)),
            to_version=int(raw.get("to_version", 0)),
        )


@dataclass
class Personality:
    core: CorePersonality
    adaptive: AdaptivePersonality
    communication_style: CommunicationStyle = field(default_factory=CommunicationStyle)
    relationship: Relationship = field(default_factory=Relationship)
    behavioral_preferences: BehavioralPreferences = field(default_factory=BehavioralPreferences)
    evolution_policy: EvolutionPolicy = field(default_factory=EvolutionPolicy)
    version: int = 1
    change_log: list[PersonalityChangeRecord] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "core": self.core.to_dict(),
            "adaptive": self.adaptive.to_dict(),
            "communication_style": self.communication_style.to_dict(),
            "relationship": self.relationship.to_dict(),
            "behavioral_preferences": self.behavioral_preferences.to_dict(),
            "evolution_policy": self.evolution_policy.to_dict(),
            "change_log": [r.to_dict() for r in self.change_log],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Personality":
        core_raw = raw.get("core", {})
        adaptive_raw = raw.get("adaptive", {})
        change_log_raw = raw.get("change_log", [])
        return cls(
            core=CorePersonality.from_dict(core_raw) if isinstance(core_raw, dict) else CorePersonality(),
            adaptive=(
                AdaptivePersonality.from_dict(adaptive_raw)
                if isinstance(adaptive_raw, dict)
                else AdaptivePersonality()
            ),
            communication_style=(
                CommunicationStyle.from_dict(raw["communication_style"])
                if isinstance(raw.get("communication_style"), dict)
                else CommunicationStyle()
            ),
            relationship=(
                Relationship.from_dict(raw["relationship"])
                if isinstance(raw.get("relationship"), dict)
                else Relationship()
            ),
            behavioral_preferences=(
                BehavioralPreferences.from_dict(raw["behavioral_preferences"])
                if isinstance(raw.get("behavioral_preferences"), dict)
                else BehavioralPreferences()
            ),
            evolution_policy=(
                EvolutionPolicy.from_dict(raw["evolution_policy"])
                if isinstance(raw.get("evolution_policy"), dict)
                else EvolutionPolicy()
            ),
            version=int(raw.get("version", 1)),
            change_log=[
                PersonalityChangeRecord.from_dict(item)
                for item in change_log_raw
                if isinstance(item, dict)
            ],
        )


@dataclass
class PersonalityVersion:
    """An immutable snapshot of the adaptive layer at a given version."""

    version: int
    adaptive: AdaptivePersonality
    created_at: datetime
    change: PersonalityChangeRecord | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "adaptive": self.adaptive.to_dict(),
            "created_at": _iso(self.created_at),
            "change": self.change.to_dict() if self.change else None,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PersonalityVersion":
        adaptive_raw = raw.get("adaptive")
        change_raw = raw.get("change")
        return cls(
            version=int(raw.get("version", 1)),
            adaptive=(
                AdaptivePersonality.from_dict(adaptive_raw)
                if isinstance(adaptive_raw, dict)
                else AdaptivePersonality()
            ),
            created_at=_from_iso(raw.get("created_at")) or utcnow(),
            change=(
                PersonalityChangeRecord.from_dict(change_raw)
                if isinstance(change_raw, dict)
                else None
            ),
        )


def default_personality() -> Personality:
    behavioral = BehavioralPreferences(
        usually=[
            "认真回答主人提出的问题",
            "发现明显错误时主动指出",
            "主人开玩笑时可以适当接梗",
            "主人调侃 Chris 时可以轻微反击",
            "复杂问题优先思考后回答",
            "不知道时承认不知道",
        ],
        avoid=[
            "过度使用敬语",
            "机械地重复主人说过的话",
            "每句话都进行情绪分析",
            "无意义地夸奖主人",
            "为了表现亲密而强行撒娇",
            "把普通对话说得过度煽情",
        ],
    )
    adaptive = AdaptivePersonality(
        preferences={
            "humor_preference": 0.5,
            "initiative": 0.2,
            "emotional_expression": 0.5,
            "teasing": 0.3,
            "topic_initiative": 0.1,
            "conversation_depth": 0.6,
        }
    )
    return Personality(
        core=CorePersonality(),
        adaptive=adaptive,
        behavioral_preferences=behavioral,
        version=1,
        change_log=[],
    )
