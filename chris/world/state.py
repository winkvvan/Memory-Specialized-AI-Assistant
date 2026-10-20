"""World State - structured snapshot of Chris's environment.

World State is the FACT layer: it records what is happening in the external
world (user presence/activity/attention, computer foreground, environment).
It is NOT Memory (Memory records durable facts/patterns) and it is NOT Behavior
(Behavior decides what Chris should do).

Legacy fields (location / current_activity / user_presence / environment /
updated_at) are kept for backward compatibility; the structured `user` /
`computer` fields are the primary state used by the World Awareness engine.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from chris.events import Event, EventBus, EventType, utcnow


class UserPresence(str, Enum):
    PRESENT = "PRESENT"
    AWAY = "AWAY"
    UNKNOWN = "UNKNOWN"


class UserActivity(str, Enum):
    IDLE = "IDLE"
    CODING = "CODING"
    BROWSING = "BROWSING"
    WATCHING = "WATCHING"
    GAMING = "GAMING"
    READING = "READING"
    WRITING = "WRITING"
    COMMUNICATING = "COMMUNICATING"
    UNKNOWN = "UNKNOWN"


class UserAttention(str, Enum):
    FOCUSED = "FOCUSED"
    DISTRACTED = "DISTRACTED"
    RELAXED = "RELAXED"
    UNKNOWN = "UNKNOWN"


class UserBusyState(str, Enum):
    BUSY = "BUSY"
    AVAILABLE = "AVAILABLE"
    UNKNOWN = "UNKNOWN"


@dataclass
class UserState:
    presence: UserPresence = UserPresence.UNKNOWN
    activity: UserActivity = UserActivity.IDLE
    attention: UserAttention = UserAttention.UNKNOWN
    estimated_state: UserBusyState = UserBusyState.UNKNOWN

    def to_dict(self) -> dict[str, Any]:
        return {
            "presence": self.presence.value,
            "activity": self.activity.value,
            "attention": self.attention.value,
            "estimated_state": self.estimated_state.value,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "UserState":
        def pick(name: str, enum_cls):
            value = raw.get(name)
            if value is None:
                return None
            try:
                return enum_cls(value)
            except ValueError:
                return None

        return cls(
            presence=pick("presence", UserPresence) or UserPresence.UNKNOWN,
            activity=pick("activity", UserActivity) or UserActivity.UNKNOWN,
            attention=pick("attention", UserAttention) or UserAttention.UNKNOWN,
            estimated_state=pick("estimated_state", UserBusyState) or UserBusyState.UNKNOWN,
        )


@dataclass
class ComputerState:
    active: bool = False
    foreground_app: str = ""
    window_title: str = ""
    idle_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "foreground_app": self.foreground_app,
            "window_title": self.window_title,
            "idle_seconds": self.idle_seconds,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ComputerState":
        known = {k for k in cls.__dataclass_fields__}
        return cls(**{k: raw[k] for k in raw if k in known})


@dataclass
class WorldState:
    # legacy display fields (kept for backward compatibility)
    location: str = "unknown"
    current_activity: str = "idle"
    user_presence: bool = False
    environment: dict[str, Any] = field(default_factory=dict)
    updated_at: datetime = field(default_factory=utcnow)
    # structured World Awareness state
    user: UserState = field(default_factory=UserState)
    computer: ComputerState = field(default_factory=ComputerState)
    activity_started_at: datetime | None = None
    last_activity_change: datetime | None = None
    _bus: EventBus | None = field(default=None, repr=False, compare=False)

    def update(self, **fields: Any) -> "WorldState":
        for key, value in fields.items():
            if key in self.__dataclass_fields__ and not key.startswith("_"):
                setattr(self, key, value)
        # mirror legacy display fields onto the structured state
        if "user_presence" in fields:
            self.user.presence = UserPresence.PRESENT if self.user_presence else UserPresence.AWAY
        if "current_activity" in fields and self.current_activity:
            try:
                self.user.activity = UserActivity(self.current_activity.upper())
            except ValueError:
                pass
        if "location" in fields:
            self.environment["location"] = self.location
        self.updated_at = utcnow()
        if self._bus:
            self._bus.publish(Event(EventType.WORLD_STATE_CHANGED, "world", self.to_dict()))
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.updated_at.isoformat(),
            "location": self.location,
            "current_activity": self.current_activity,
            "user_presence": self.user_presence,
            "environment": dict(self.environment),
            "user": self.user.to_dict(),
            "computer": self.computer.to_dict(),
            "activity_started_at": self.activity_started_at.isoformat() if self.activity_started_at else None,
            "last_activity_change": self.last_activity_change.isoformat() if self.last_activity_change else None,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "WorldState":
        known = {k for k in cls.__dataclass_fields__ if not k.startswith("_") and k != "updated_at"}
        kwargs = {k: v for k, v in raw.items() if k in known}
        if isinstance(raw.get("user"), dict):
            kwargs["user"] = UserState.from_dict(raw["user"])
        if isinstance(raw.get("computer"), dict):
            kwargs["computer"] = ComputerState.from_dict(raw["computer"])
        state = cls(**kwargs)
        state.updated_at = datetime.fromisoformat(raw.get("timestamp") or raw.get("updated_at")) if (raw.get("timestamp") or raw.get("updated_at")) else utcnow()
        if raw.get("activity_started_at"):
            state.activity_started_at = datetime.fromisoformat(raw["activity_started_at"])
        if raw.get("last_activity_change"):
            state.last_activity_change = datetime.fromisoformat(raw["last_activity_change"])
        return state


def load_world_state(path: Path) -> WorldState:
    if not path.exists():
        return WorldState()
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return WorldState()
    return WorldState.from_dict(raw)


def save_world_state(state: WorldState, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
