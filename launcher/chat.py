"""Application layer: builds the Chris runtime and runs chat turns.

The UI never talks to a Provider or state file directly - it calls these
functions, which go through the Core (chris.app / chris.events / ...).
"""

from __future__ import annotations

import importlib
import threading

_DYNAMIC = (
    "chris.app",
    "chris.events",
    "chris.behavior.engine",
    "chris.memory.base",
    "chris.config",
    "chris.tools.pipeline",
)


def _mod(name: str):
    return importlib.import_module(name)


def build_chat_pipeline(config=None, provider=None, voice_chat_runner=None):
    """Build the Chris runtime. Pass a Config and/or provider for tests."""
    cfg = config if config is not None else _mod("chris.config").Config.load()
    if provider is not None:
        runtime = _mod("chris.app").build_runtime(cfg, provider=provider)
    else:
        runtime = _mod("chris.app").build_runtime(cfg)
    _wire_voice(runtime, voice_chat_runner)
    return runtime


class RealVoiceChatRunner:
    """Routes ASR text into the normal (blocking) Chris conversation.

    This is the single integration point between the Voice Pipeline and Chris:
    nothing here reimplements dialogue logic; it just calls the existing
    chat_turn (which already drives tools / memory / permission / confirmation).
    """

    def __init__(self, runtime) -> None:
        self.runtime = runtime

    def run(self, user_text: str, session) -> str:
        reply = chat_turn(self.runtime, user_text)
        return reply or ""


def _wire_voice(runtime, voice_chat_runner=None) -> None:
    """Attach the voice chat runner (and any injected runner) to the runtime."""
    runner = voice_chat_runner or RealVoiceChatRunner(runtime)
    voice = getattr(runtime, "voice", None)
    if voice is not None:
        voice.set_chat_runner(runner)


def _prepare_turn(runtime, user_text: str, cancel, exclude_since):
    """Shared pre-step: session ensure + memory write + remember extraction.

    Returns (messages, remembered) or None if the behavior engine said no action.
    """
    if cancel is not None and cancel.is_set():
        return None
    if not runtime.temporal.session_active():
        runtime.temporal.start_session()
    events = _mod("chris.events")
    behavior = _mod("chris.behavior.engine")
    memory = _mod("chris.memory.base")
    app = _mod("chris.app")

    runtime.bus.publish(events.Event(events.EventType.USER_MESSAGE, "gui", {"content": user_text}))
    runtime.temporal.record_interaction()
    just_added = runtime.memory.add(
        memory.MemoryEntry(kind="conversation", content=f"user: {user_text}", metadata={"role": "user"})
    )
    _remember_from(runtime, user_text)
    decision = runtime.behavior.decide(app.build_behavior_context(runtime, has_user_message=True))
    if decision.action is behavior.BehaviorAction.NO_ACTION:
        return None
    _run_recall_intent(runtime, user_text, just_added)
    messages = [
        {
            "role": "system",
            "content": app.build_system_prompt(
                runtime, exclude_since=exclude_since, query_text=user_text
            ),
        }
    ]
    history = None
    return messages, True


def _run_recall_intent(runtime, user_text: str, just_added) -> None:
    """LLM-based recall-intent understanding (7.4): explicit re-confirmation
    REINFORCE + 'asking about the past' archived retrieval. Best-effort:
    any failure is ignored so the conversation never breaks."""
    try:
        app = _mod("chris.app")
        judge = getattr(app, "process_recall_intent", None)
        if judge is not None:
            excluded = {just_added.memory_id} if just_added is not None else frozenset()
            judge(runtime, user_text, exclude_memory_ids=excluded)
    except Exception:  # noqa: BLE001
        pass


def _remember_from(runtime, user_text: str) -> None:
    """Extract explicit remember requests (LLM-first, heuristic fallback).

    Goes through the gated MemoryOperationProposal layer: the LLM proposes
    CREATE_MEMORY, the memory policy validates, the store executes.
    """
    app = _mod("chris.app")
    ecology = _mod("chris.memory.ecology")
    facts = app.extract_remember_with_llm(runtime.provider, user_text)
    if not facts:
        fallback = app.extract_remember(user_text)
        if fallback:
            facts = [fallback]
    for fact in facts:
        runtime.memory.propose(
            ecology.MemoryOperationProposal(
                operation=ecology.MemoryOperation.CREATE.value,
                content=fact,
                memory_type="",
                remember=True,
                source="EXPLICIT_USER",
                reason="user asked to remember",
            )
        )


def chat_turn(runtime, user_text: str, cancel=None, history=None, exclude_since=None):
    """Blocking turn (kept for tests / compatibility).

    Routes through the tool pipeline when tools are enabled; otherwise behaves
    exactly like a plain provider.generate call.
    """
    prepared = _prepare_turn(runtime, user_text, cancel, exclude_since)
    if prepared is None:
        return None
    messages, _remembered = prepared
    if history:
        messages.extend(history[-40:])
    messages.append({"role": "user", "content": user_text})
    if cancel is not None and cancel.is_set():
        return None
    reply = _generate_reply(runtime, messages)
    if cancel is not None and cancel.is_set():
        return None
    _record_reply(runtime, reply)
    return reply


def _generate_reply(runtime, messages):
    """Try the tool pipeline first; fall back to a plain provider call.

    When tools are enabled and the LLM actually emits tool calls, the pipeline
    runs them and returns the final text. Otherwise (no tools, or no tool call)
    it returns None and we fall back to the exact legacy behaviour.
    """
    pipeline = _mod("chris.tools.pipeline")
    reply = pipeline.run_tool_pipeline(
        runtime, messages, max_tokens=runtime.config.max_response_tokens
    )
    if reply is not None:
        return reply
    return runtime.provider.generate(
        messages, max_tokens=runtime.config.max_response_tokens
    )


def stream_turn(
    runtime,
    user_text: str,
    cancel: threading.Event,
    history=None,
    exclude_since=None,
    on_chunk=None,
):
    """Streaming turn. Yields nothing; calls on_chunk(str) incrementally.

    Returns the full reply, or None if cancelled / no action.

    When tools are enabled and registered, the turn is routed through the Tool
    Pipeline (which supports tools + permission confirmation), streaming the
    final text. When tools are not involved, the original PDE streaming path is
    used so plain text chat keeps its latency profile.
    """
    prepared = _prepare_turn(runtime, user_text, cancel, exclude_since)
    if prepared is None:
        return None
    messages, _remembered = prepared

    # If tools are enabled and registered, route through the tool pipeline.
    tools_active = _tools_active(runtime)
    if tools_active:
        if history:
            messages.extend(history[-40:])
        messages.append({"role": "user", "content": user_text})
        if cancel is not None and cancel.is_set():
            return None
        pipeline = _mod("chris.tools.pipeline")
        reply = pipeline.run_tool_pipeline(
            runtime, messages, max_tokens=runtime.config.max_response_tokens
        )
        if reply is None:
            # no tools actually engaged -> plain generate
            reply = runtime.provider.generate(
                messages, max_tokens=runtime.config.max_response_tokens
            )
        if cancel is not None and cancel.is_set():
            return None
        _stream_plain(reply, on_chunk)
        _record_reply(runtime, reply)
        return reply

    if history:
        messages.extend(history[-40:])
    messages.append({"role": "user", "content": user_text})
    if cancel is not None and cancel.is_set():
        return None
    reply = ""
    for chunk in runtime.provider.stream(messages, max_tokens=runtime.config.max_response_tokens):
        if cancel is not None and cancel.is_set():
            return None
        reply += chunk
        if on_chunk:
            on_chunk(chunk)
    if cancel is not None and cancel.is_set():
        return None
    _record_reply(runtime, reply)
    return reply


def _tools_active(runtime) -> bool:
    """True when the Tool Pipeline is enabled and has registered tools."""
    try:
        cfg_tools = runtime.config.tools or {}
        if not cfg_tools.get("enabled", True):
            return False
        registry = getattr(runtime, "tool_registry", None)
        executor = getattr(runtime, "tool_executor", None)
        if registry is None or executor is None:
            return False
        if not hasattr(runtime.provider, "generate_with_tools"):
            return False
        return len(registry.definitions()) > 0
    except Exception:  # noqa: BLE001
        return False


def _stream_plain(reply: str, on_chunk) -> None:
    if not on_chunk:
        return
    if not reply:
        return
    # stream in reasonable chunks to keep the UI alive
    chunk_size = 8
    for i in range(0, len(reply), chunk_size):
        on_chunk(reply[i : i + chunk_size])


def _record_reply(runtime, reply: str) -> None:
    events = _mod("chris.events")
    memory = _mod("chris.memory.base")
    runtime.memory.add(
        memory.MemoryEntry(kind="conversation", content=f"chris: {reply}", metadata={"role": "assistant"})
    )
    runtime.bus.publish(events.Event(events.EventType.CHRIS_MESSAGE, "gui", {"content": reply}))
