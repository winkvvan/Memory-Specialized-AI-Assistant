"""Tool pipeline: the LLM <-> Tool loop.

When tools are enabled and registered, the chat flow routes through this module:
   LLM -> parse tool calls -> registry lookup -> validation -> permission ->
   execution -> ToolResult -> conversation context -> LLM (until no more calls).
Serial execution only (safe, deterministic). When tools are disabled or there
are no tool calls, behaviour is identical to a plain provider.generate call.
"""

from __future__ import annotations

from typing import Any, Callable

from chris.tools.adapter import ToolCallAdapter
from chris.tools.base import ToolContext
from chris.tools.executor import ToolExecutor
from chris.tools.registry import ToolRegistry

MAX_TOOL_ROUNDS = 3


class ToolLoopError(RuntimeError):
    pass


def build_tool_context(runtime, cancel_event=None) -> ToolContext:
    """Project the pieces of the Runtime that tools may safely read.

    Never the Runtime object itself: a minimal, explicit dependency set.
    """
    temporal = getattr(runtime, "temporal", None)
    world = getattr(runtime, "world", None)
    memory = getattr(runtime, "memory", None)
    now = temporal.now() if temporal is not None else None
    memory_context: dict[str, Any] = {}
    try:
        memory_context = {"recent": len(memory.list_recent(limit=5))}
    except Exception:  # noqa: BLE001
        pass
    world_context: dict[str, Any] = {}
    try:
        world_context = {
            "location": world.location,
            "activity": world.current_activity,
            "user_presence": world.user_presence,
        }
    except Exception:  # noqa: BLE001
        pass
    return ToolContext(
        now=now,
        session="active" if temporal is not None and temporal.session_active() else "",
        memory_context=memory_context,
        world_context=world_context,
        project_context={},
        permission_context={},
        logger=None,
        bus=getattr(runtime, "bus", None),
        cancel_event=cancel_event,
    )


def run_tool_pipeline(
    runtime,
    messages: list[dict[str, Any]],
    *,
    max_tokens: int | None = None,
    context_factory: Callable[[], Any] | None = None,
    confirm: Callable[[Any], bool] | None = None,
) -> str | None:
    """Run one chat turn through the tool pipeline.

    Returns the final reply text, or None when tools are not enabled / no tools
    are registered (caller should fall back to provider.generate directly).

    ``confirm`` (optional) is invoked when a tool call needs user confirmation.
    It receives a ConfirmationRequest and returns True to approve (the executor
    then re-gates and executes) or False to deny. Without ``confirm``, a
    confirmation_required result is passed through to the LLM unchanged.
    """
    registry: ToolRegistry | None = getattr(runtime, "tool_registry", None)
    executor: ToolExecutor | None = getattr(runtime, "tool_executor", None)
    if registry is None or executor is None:
        return None

    cfg_tools = (getattr(runtime.config, "tools", None) or {}) if getattr(runtime, "config", None) else {}
    if not cfg_tools.get("enabled", True):
        return None

    definitions = registry.definitions()
    if not definitions:
        return None

    provider = runtime.provider
    if not hasattr(provider, "generate_with_tools"):
        return None
    provider_tools = ToolCallAdapter.to_provider_tools(definitions)
    make_context = context_factory or (lambda: build_tool_context(runtime))

    response = provider.generate_with_tools(
        messages, tools=provider_tools, max_tokens=max_tokens
    )
    rounds = 0
    while response.tool_calls and rounds < MAX_TOOL_ROUNDS:
        rounds += 1
        calls = ToolCallAdapter.to_tool_calls(response)
        context = make_context()
        results = executor.execute_many(calls, context)
        if confirm is not None:
            results = _resolve_confirmations(executor, calls, context, results, confirm)
        messages.append(
            {
                "role": "assistant",
                "content": response.content or "",
                "tool_calls": _assistant_tool_calls(calls),
            }
        )
        for call, result in zip(calls, results):
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.call_id,
                    "content": result.content or result.error or "",
                }
            )
        response = provider.generate_with_tools(
            messages, tools=provider_tools, max_tokens=max_tokens
        )
    return response.content


def _resolve_confirmations(executor, calls, context, results, confirm) -> list:
    """For each confirmation_required result, ask the confirm callback and,
    if approved, re-execute via the approved path."""
    out = []
    for call, result in zip(calls, results):
        metadata = getattr(result, "metadata", {}) or {}
        confirmation_id = metadata.get("confirmation_id")
        if metadata.get("error_code") == "confirmation_required" and confirmation_id:
            manager = getattr(executor, "confirmation", None)
            request = manager.get_request(confirmation_id) if manager else None
            if request is not None and confirm(request):
                out.append(executor.execute_approved(call, context, confirmation_id))
                continue
        out.append(result)
    return out


def _assistant_tool_calls(calls) -> list[dict[str, Any]]:
    return [
        {
            "id": call.call_id,
            "type": "function",
            "function": {
                "name": call.tool_name,
                "arguments": _json_arguments(call.arguments),
            },
        }
        for call in calls
    ]


def _json_arguments(arguments: dict[str, Any]) -> str:
    import json

    return json.dumps(arguments, ensure_ascii=False)
