"""ToolRegistry: the single source of truth for available tools.

No module keeps its own tool list - tools are registered here once and looked
up by name. Handles duplicate registration (raise), unknown tools (None / raise
on request) and disabled tools (excluded from the provider's tool list).
"""

from __future__ import annotations

from chris.tools.base import Tool
from chris.tools.models import ToolDefinition


class DuplicateToolError(ValueError):
    pass


class UnknownToolError(KeyError):
    pass


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool, *, replace: bool = False) -> None:
        if tool.name in self._tools and not replace:
            raise DuplicateToolError(f"tool already registered: {tool.name!r}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def require(self, name: str) -> Tool:
        tool = self._tools.get(name)
        if tool is None:
            raise UnknownToolError(f"unknown tool: {name!r}")
        return tool

    def remove(self, name: str) -> Tool | None:
        return self._tools.pop(name, None)

    def list(self) -> list[Tool]:
        return sorted(self._tools.values(), key=lambda tool: tool.name)

    def definitions(self) -> list[ToolDefinition]:
        return [tool.definition() for tool in self.list() if tool.enabled]

    def is_enabled(self, name: str) -> bool:
        tool = self._tools.get(name)
        return tool is not None and tool.enabled

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)
