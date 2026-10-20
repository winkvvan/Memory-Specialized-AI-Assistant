"""Unified interface for LLM providers.

Business logic must never call a concrete model API directly. It only talks to
:class:`LLMProvider`. Swap providers without touching personality, memory,
time, world or behavior modules.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterator, Sequence, TypedDict


class Message(TypedDict):
    role: str
    content: str


@dataclass
class LLMResponse:
    """Vendor-neutral response: reply text plus optional tool calls.

    Tool calls are normalized to a simple list of dicts with at least the keys
    ``id``, ``name`` and ``arguments`` (parsed dict, or raw JSON string). The
    Tool Core adapter converts these into internal ``ToolCall`` objects; vendor
    JSON never reaches the core directly.
    """

    content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


class LLMProvider(ABC):
    name: str = "abstract"

    @abstractmethod
    def generate(self, messages: Sequence[Message], **kwargs: object) -> str:
        """Return a single complete reply for the given messages."""

    @abstractmethod
    def stream(self, messages: Sequence[Message], **kwargs: object) -> Iterator[str]:
        """Yield reply text incrementally."""

    @abstractmethod
    def health_check(self) -> bool:
        """Return True when the provider can serve requests."""

    def generate_with_tools(
        self,
        messages: Sequence[Message],
        tools: Sequence[dict[str, Any]] | None = None,
        **kwargs: object,
    ) -> LLMResponse:
        """Generate a reply, optionally offering tools to the model.

        Default implementation ignores tools and returns a plain text response
        (no tool calls) - every existing provider keeps working unchanged.
        Providers that support tool calling override this to parse tool calls.
        """
        content = self.generate(messages, **kwargs)
        return LLMResponse(content=content, tool_calls=[])
