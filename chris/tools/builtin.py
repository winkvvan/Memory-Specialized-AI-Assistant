"""Built-in, side-effect-free test tools (Phase 8.1).

These exist so the whole Tool Core is exercised through the registry without
touching the network or the file system: get_current_time and echo.
"""

from __future__ import annotations

from typing import Any

from chris.tools.base import Tool, ToolContext
from chris.tools.models import RiskLevel, ToolResult, ToolStatus


class GetCurrentTimeTool(Tool):
    name = "get_current_time"
    description = "Return Chris's current local time."
    permission = RiskLevel.READ_ONLY
    risk_level = RiskLevel.READ_ONLY
    input_schema = {
        "type": "object",
        "properties": {
            "format": {
                "type": "string",
                "enum": ["iso", "natural"],
                "description": "iso or natural (YYYY-MM-DD HH:MM)",
            }
        },
        "required": [],
    }

    def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        fmt = arguments.get("format", "iso")
        now = context.now
        if fmt == "natural":
            content = now.strftime("%Y-%m-%d %H:%M")
        else:
            content = now.isoformat()
        return ToolResult(
            tool_name=self.name,
            status=ToolStatus.SUCCESS,
            content=content,
            metadata={"time": now.isoformat()},
        )


class EchoTool(Tool):
    name = "echo"
    description = "Return the input text back unchanged."
    permission = RiskLevel.LOW
    risk_level = RiskLevel.LOW
    input_schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        return ToolResult(
            tool_name=self.name,
            status=ToolStatus.SUCCESS,
            content=str(arguments.get("text", "")),
            metadata={"echoed": True},
        )
