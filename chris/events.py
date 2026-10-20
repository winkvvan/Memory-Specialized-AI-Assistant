"""Event system for Chris.

Every important change in the system produces an :class:`Event`. Events are
decoupled from any concrete LLM provider and flow through a single
:class:`EventBus`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable

Handler = Callable[["Event"], None]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EventType(str, Enum):
    SESSION_STARTED = "SESSION_STARTED"
    SESSION_ENDED = "SESSION_ENDED"
    USER_MESSAGE = "USER_MESSAGE"
    CHRIS_MESSAGE = "CHRIS_MESSAGE"
    ACTIVITY_STARTED = "ACTIVITY_STARTED"
    ACTIVITY_STOPPED = "ACTIVITY_STOPPED"
    TIME_THRESHOLD_REACHED = "TIME_THRESHOLD_REACHED"
    MEMORY_CREATED = "MEMORY_CREATED"
    MEMORY_UPDATED = "MEMORY_UPDATED"
    MEMORY_ACCESSED = "MEMORY_ACCESSED"
    MEMORY_REINFORCED = "MEMORY_REINFORCED"
    MEMORY_WEAKENED = "MEMORY_WEAKENED"
    MEMORY_DECAYED = "MEMORY_DECAYED"
    MEMORY_DORMANT = "MEMORY_DORMANT"
    MEMORY_RECOVERED = "MEMORY_RECOVERED"
    MEMORY_SUPERSEDED = "MEMORY_SUPERSEDED"
    MEMORY_INVALIDATED = "MEMORY_INVALIDATED"
    MEMORY_CONTRADICTED = "MEMORY_CONTRADICTED"
    MEMORY_ARCHIVED = "MEMORY_ARCHIVED"
    MEMORY_COMPRESSED = "MEMORY_COMPRESSED"
    MEMORY_GARBAGE_COLLECTED = "MEMORY_GARBAGE_COLLECTED"
    MEMORY_FORGOTTEN = "MEMORY_FORGOTTEN"
    MEMORY_RESTORED = "MEMORY_RESTORED"
    MEMORY_MAINTENANCE_CHECKPOINT = "MEMORY_MAINTENANCE_CHECKPOINT"
    PERSONALITY_CHANGED = "PERSONALITY_CHANGED"
    WORLD_STATE_CHANGED = "WORLD_STATE_CHANGED"
    WORLD_OBSERVATION = "WORLD_OBSERVATION"
    USER_PRESENCE_CHANGED = "USER_PRESENCE_CHANGED"
    USER_ACTIVITY_CHANGED = "USER_ACTIVITY_CHANGED"
    USER_ATTENTION_CHANGED = "USER_ATTENTION_CHANGED"
    COMPUTER_STATE_CHANGED = "COMPUTER_STATE_CHANGED"
    FOREGROUND_APP_CHANGED = "FOREGROUND_APP_CHANGED"
    USER_IDLE_STARTED = "USER_IDLE_STARTED"
    USER_IDLE_ENDED = "USER_IDLE_ENDED"
    TIME_TICK = "TIME_TICK"
    TIME_PASSED = "TIME_PASSED"
    TIME_SKIPPED = "TIME_SKIPPED"
    IDLE_STARTED = "IDLE_STARTED"
    IDLE_ENDED = "IDLE_ENDED"
    LONG_IDLE = "LONG_IDLE"
    LONG_SESSION = "LONG_SESSION"
    LONG_ABSENCE = "LONG_ABSENCE"
    DAY_CHANGED = "DAY_CHANGED"
    WEEK_CHANGED = "WEEK_CHANGED"
    MONTH_CHANGED = "MONTH_CHANGED"
    TOOL_CALL_STARTED = "TOOL_CALL_STARTED"
    TOOL_CALL_COMPLETED = "TOOL_CALL_COMPLETED"
    TOOL_CALL_FAILED = "TOOL_CALL_FAILED"
    TOOL_CALL_DENIED = "TOOL_CALL_DENIED"
    TOOL_CALL_CANCELLED = "TOOL_CALL_CANCELLED"
    CODING_TASK_STARTED = "CODING_TASK_STARTED"
    CODING_TASK_PROGRESS = "CODING_TASK_PROGRESS"
    CODING_TASK_COMPLETED = "CODING_TASK_COMPLETED"
    CODING_TASK_FAILED = "CODING_TASK_FAILED"
    CODING_TASK_CANCELLED = "CODING_TASK_CANCELLED"
    CODING_TASK_DENIED = "CODING_TASK_DENIED"
    COMPUTER_ACTION_STARTED = "COMPUTER_ACTION_STARTED"
    COMPUTER_ACTION_COMPLETED = "COMPUTER_ACTION_COMPLETED"
    COMPUTER_ACTION_FAILED = "COMPUTER_ACTION_FAILED"
    COMPUTER_ACTION_CANCELLED = "COMPUTER_ACTION_CANCELLED"
    COMPUTER_ACTION_DENIED = "COMPUTER_ACTION_DENIED"
    CONFIRMATION_REQUESTED = "CONFIRMATION_REQUESTED"
    CONFIRMATION_APPROVED = "CONFIRMATION_APPROVED"
    CONFIRMATION_DENIED = "CONFIRMATION_DENIED"
    CONFIRMATION_EXPIRED = "CONFIRMATION_EXPIRED"
    CONFIRMATION_CANCELLED = "CONFIRMATION_CANCELLED"
    CONFIRMATION_CONSUMED = "CONFIRMATION_CONSUMED"
    CONFIRMATION_FAILED = "CONFIRMATION_FAILED"
    VOICE_SESSION_STARTED = "VOICE_SESSION_STARTED"
    VOICE_LISTENING_STARTED = "VOICE_LISTENING_STARTED"
    VOICE_TRANSCRIPTION_STARTED = "VOICE_TRANSCRIPTION_STARTED"
    VOICE_TRANSCRIPTION_COMPLETED = "VOICE_TRANSCRIPTION_COMPLETED"
    VOICE_THINKING_STARTED = "VOICE_THINKING_STARTED"
    VOICE_SPEAKING_STARTED = "VOICE_SPEAKING_STARTED"
    VOICE_SPEAKING_COMPLETED = "VOICE_SPEAKING_COMPLETED"
    VOICE_SESSION_COMPLETED = "VOICE_SESSION_COMPLETED"
    VOICE_SESSION_FAILED = "VOICE_SESSION_FAILED"
    VOICE_SESSION_CANCELLED = "VOICE_SESSION_CANCELLED"


@dataclass(frozen=True)
class Event:
    type: EventType
    source: str
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=utcnow)
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp.isoformat(),
            "type": self.type.value,
            "source": self.source,
            "payload": self.payload,
        }


class EventBus:
    """Minimal synchronous publish/subscribe bus."""

    def __init__(self) -> None:
        self._handlers: dict[EventType, list[Handler]] = {}
        self._wildcards: list[Handler] = []

    def subscribe(
        self,
        handler: Handler,
        event_types: EventType | list[EventType] | None = None,
    ) -> None:
        if event_types is None:
            self._wildcards.append(handler)
            return
        types = [event_types] if isinstance(event_types, EventType) else list(event_types)
        for event_type in types:
            self._handlers.setdefault(event_type, []).append(handler)

    def unsubscribe(
        self,
        handler: Handler,
        event_types: EventType | list[EventType] | None = None,
    ) -> None:
        if event_types is None:
            if handler in self._wildcards:
                self._wildcards.remove(handler)
            return
        types = [event_types] if isinstance(event_types, EventType) else list(event_types)
        for event_type in types:
            handlers = self._handlers.get(event_type)
            if handlers and handler in handlers:
                handlers.remove(handler)

    def publish(self, event: Event) -> None:
        for handler in tuple(self._wildcards):
            handler(event)
        for handler in tuple(self._handlers.get(event.type, ())):
            handler(event)
