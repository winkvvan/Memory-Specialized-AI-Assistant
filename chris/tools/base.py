"""Tool interface and the minimal dependency context tools receive."""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from chris.events import EventBus, utcnow
from chris.tools.models import RiskLevel, ToolDefinition, ToolResult


@dataclass
class ToolContext:
    """What a tool is allowed to see: a minimal, explicit dependency set.

    Never the whole Runtime / Application object. `now` is the current time
    (from Temporal), the context dicts are projections of state, `bus` allows
    emitting events, and `cancel_event` enables cooperative cancellation.
    """

    now: datetime = field(default_factory=utcnow)
    session: str = ""
    memory_context: dict[str, Any] = field(default_factory=dict)
    world_context: dict[str, Any] = field(default_factory=dict)
    project_context: dict[str, Any] = field(default_factory=dict)
    permission_context: dict[str, Any] = field(default_factory=dict)
    logger: Any = None
    bus: EventBus | None = None
    cancel_event: threading.Event | None = None
    approved: bool = False


class Tool(ABC):
    """Executable tool. `execute` returns a ToolResult and must not raise:
    the executor converts any exception into a structured failure result."""

    name: str = ""
    description: str = ""
    input_schema: dict[str, Any] = {}
    enabled: bool = True
    permission: RiskLevel = RiskLevel.LOW
    risk_level: RiskLevel = RiskLevel.LOW

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            input_schema=dict(self.input_schema),
            enabled=self.enabled,
            permission=self.permission,
            risk_level=self.risk_level,
        )

    @abstractmethod
    def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        """Run the tool. Must return a ToolResult."""

    def __repr__(self) -> str:
        return f"<Tool {self.name!r}>"
