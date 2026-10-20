"""Confirmation data model."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from chris.events import utcnow


class ConfirmationStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    DENIED = "DENIED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    CONSUMED = "CONSUMED"


@dataclass
class ConfirmationRequest:
    confirmation_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    call_id: str = ""
    tool_name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    risk_level: str = ""
    reason: str = ""
    source: str = "llm"
    status: ConfirmationStatus = ConfirmationStatus.PENDING
    created_at: datetime = field(default_factory=utcnow)
    expires_at: datetime = field(default_factory=utcnow)
    decided_at: datetime | None = None
    decided_by: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_expired(self, now: datetime | None = None) -> bool:
        now = now or utcnow()
        return self.status is ConfirmationStatus.PENDING and now >= self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "confirmation_id": self.confirmation_id,
            "call_id": self.call_id,
            "tool_name": self.tool_name,
            "risk_level": self.risk_level,
            "reason": self.reason,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "decided_at": self.decided_at.isoformat() if self.decided_at else None,
            "decided_by": self.decided_by,
        }

    def safe_summary(self) -> dict[str, Any]:
        """Logging-safe summary: never includes arguments (which may hold
        sensitive input like type_text)."""
        return {
            "confirmation_id": self.confirmation_id,
            "call_id": self.call_id,
            "tool_name": self.tool_name,
            "risk_level": self.risk_level,
            "status": self.status.value,
        }
