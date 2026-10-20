"""Adapter between the LLM Provider layer and the Tool Core.

The provider returns a vendor-neutral ``LLMResponse`` (content + tool_calls).
This adapter converts those tool calls into the internal ``ToolCall`` protocol
and converts ``ToolDefinition`` into the provider-agnostic `tools` payload the
OpenAI-compatible chat API expects. Core never sees vendor JSON directly.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Iterable

from chris.llm.base import LLMResponse
from chris.tools.models import ToolCall, ToolDefinition


class ToolCallAdapter:
    @staticmethod
    def to_tool_calls(response: LLMResponse, source: str = "llm") -> list[ToolCall]:
        calls: list[ToolCall] = []
        for raw in response.tool_calls:
            name = str(raw.get("name") or "")
            arguments = raw.get("arguments", {})
            if isinstance(arguments, str):
                try:
                    parsed = json.loads(arguments)
                    arguments = parsed if isinstance(parsed, dict) else {"_value": parsed}
                except json.JSONDecodeError:
                    arguments = {"_raw": arguments}
            calls.append(
                ToolCall(
                    call_id=str(raw.get("id") or uuid.uuid4().hex),
                    tool_name=name,
                    arguments=arguments,
                    source=source,
                )
            )
        return calls

    @staticmethod
    def to_provider_tools(
        definitions: Iterable[ToolDefinition],
    ) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": definition.name,
                    "description": definition.description,
                    "parameters": definition.input_schema or {"type": "object"},
                },
            }
            for definition in definitions
        ]
