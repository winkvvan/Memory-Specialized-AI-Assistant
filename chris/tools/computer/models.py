"""Computer action data models.

Backend-internal state never becomes the Chris protocol - these are the
Chris-side structures used to schedule, track and report desktop actions.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from chris.events import utcnow


class ComputerStatus(str, Enum):
    PENDING = "PENDING"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMEOUT = "TIMEOUT"
    DENIED = "DENIED"


@dataclass
class ComputerAction:
    action_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    action_type: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    status: ComputerStatus = ComputerStatus.PENDING
    created_at: datetime = field(default_factory=utcnow)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    source: str = "llm"
    risk_level: str = "LOW"
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        iso = lambda d: d.isoformat() if d else None
        return {
            "action_id": self.action_id,
            "action_type": self.action_type,
            "arguments": dict(self.arguments),
            "status": self.status.value,
            "created_at": iso(self.created_at),
            "started_at": iso(self.started_at),
            "completed_at": iso(self.completed_at),
            "source": self.source,
            "risk_level": self.risk_level,
            "error": self.error,
            "metadata": dict(self.metadata),
        }


@dataclass
class ComputerActionResult:
    status: ComputerStatus
    action_id: str = ""
    action_type: str = ""
    error: str | None = None
    error_code: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    duration_seconds: float = 0.0

    def to_text(self) -> str:
        lines = [f"Computer action {self.action_type} {self.status.value.lower()}."]
        if self.error:
            lines.append(f"Reason: {self.error}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "action_id": self.action_id,
            "action_type": self.action_type,
            "error": self.error,
            "error_code": self.error_code,
            "metadata": dict(self.metadata),
            "duration_seconds": self.duration_seconds,
        }
