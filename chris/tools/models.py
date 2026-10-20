"""Tool data models: the internal, model-independent protocol.

Vendor-specific tool-call JSON never reaches this layer directly - the Provider
Adapter converts it into :class:`ToolCall` first. Everything here is plain data
that business code can reason about.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from chris.events import utcnow


class ToolStatus(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    DENIED = "denied"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


class RiskLevel(str, Enum):
    READ_ONLY = "READ_ONLY"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class ToolDefinition:
    """Static description of a registered tool."""

    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    permission: RiskLevel = RiskLevel.LOW
    risk_level: RiskLevel = RiskLevel.LOW

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": dict(self.input_schema),
            "enabled": self.enabled,
            "permission": self.permission.value,
            "risk_level": self.risk_level.value,
        }


@dataclass
class ToolCall:
    """Chris's internal representation of one requested tool invocation."""

    call_id: str
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=utcnow)
    source: str = "llm"

    @classmethod
    def new(
        cls,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        source: str = "llm",
    ) -> "ToolCall":
        return cls(
            call_id=uuid.uuid4().hex,
            tool_name=tool_name,
            arguments=arguments or {},
            source=source,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "tool_name": self.tool_name,
            "arguments": dict(self.arguments),
            "timestamp": self.timestamp.isoformat(),
            "source": self.source,
        }


@dataclass
class ToolResult:
    """Structured outcome of a tool execution."""

    call_id: str = ""
    tool_name: str = ""
    status: ToolStatus = ToolStatus.SUCCESS
    content: str = ""
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    duration_seconds: float = 0.0

    @classmethod
    def success(
        cls,
        tool_name: str,
        content: str = "",
        call_id: str = "",
        **metadata: Any,
    ) -> "ToolResult":
        return cls(call_id=call_id, tool_name=tool_name, content=content, metadata=metadata)

    @classmethod
    def failure(
        cls,
        tool_name: str,
        error: str,
        call_id: str = "",
        **metadata: Any,
    ) -> "ToolResult":
        return cls(
            call_id=call_id,
            tool_name=tool_name,
            status=ToolStatus.FAILURE,
            error=error,
            metadata=metadata,
        )

    @property
    def ok(self) -> bool:
        return self.status is ToolStatus.SUCCESS

    def to_dict(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "tool_name": self.tool_name,
            "status": self.status.value,
            "content": self.content,
            "error": self.error,
            "metadata": dict(self.metadata),
            "duration_seconds": self.duration_seconds,
        }
