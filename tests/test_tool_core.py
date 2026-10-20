"""Phase 8.1 Tool Core tests (offline, no network).

Covers: ToolDefinition / ToolCall / ToolResult, Registry (register/get/list/
remove, duplicate/unknown/disabled), schema validation (valid/invalid/missing/
unexpected/malformed), execution (success/failure/exception/timeout/cancelled/
denied), permission, EventBus integration, Temporal activity, Provider Adapter
(LLM -> ToolCall) and the full tool pipeline.
"""

import threading
import unittest
from datetime import datetime, timezone
from unittest import mock

from chris.config import Config
from chris.events import EventBus, EventType
from chris.llm.base import LLMResponse
from chris.tools import RiskLevel, Tool, ToolCall, ToolContext, ToolDefinition, ToolResult, ToolStatus
from chris.tools.adapter import ToolCallAdapter
from chris.tools.builtin import EchoTool, GetCurrentTimeTool
from chris.tools.executor import ToolExecutor
from chris.tools.permission import DefaultPermissionPolicy, PermissionPolicy, PermissionDecision
from chris.tools.pipeline import run_tool_pipeline
from chris.tools.registry import DuplicateToolError, ToolRegistry, UnknownToolError
from chris.tools.validation import validate_arguments


def _context(**kwargs):
    return ToolContext(now=datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc), **kwargs)


class _FailTool(Tool):
    name = "fail"
    description = "always raises"

    def execute(self, context, arguments):
        raise RuntimeError("boom")


class _HangTool(Tool):
    name = "hang"
    description = "sleeps forever"

    def execute(self, context, arguments):
        import time

        time.sleep(60)
        return ToolResult.success("hang", "never")


class _BlockingTool(Tool):
    name = "blocking"
    description = "waits on cancel event"
    input_schema = {"type": "object", "properties": {}, "required": []}

    def execute(self, context, arguments):
        if context.cancel_event is not None:
            while not context.cancel_event.is_set():
                context.cancel_event.wait(0.01)
            return ToolResult(
                tool_name=self.name, status=ToolStatus.CANCELLED, error="cancelled"
            )
        return ToolResult.success("blocking", "done")


class _HighRiskTool(EchoTool):
    name = "dangerous_echo"
    risk_level = RiskLevel.CRITICAL
    permission = RiskLevel.CRITICAL


class TestModels(unittest.TestCase):
    def test_tool_definition_fields(self):
        definition = ToolDefinition(
            name="x",
            description="desc",
            input_schema={"type": "object"},
            enabled=True,
            permission=RiskLevel.LOW,
            risk_level=RiskLevel.READ_ONLY,
        )
        self.assertEqual(definition.name, "x")
        self.assertTrue(definition.enabled)
        data = definition.to_dict()
        self.assertEqual(data["permission"], "LOW")
        self.assertEqual(data["risk_level"], "READ_ONLY")

    def test_tool_call_new_generates_call_id(self):
        call = ToolCall.new("echo", {"text": "hi"})
        self.assertTrue(call.call_id)
        self.assertEqual(call.tool_name, "echo")
        self.assertEqual(call.arguments, {"text": "hi"})
        self.assertEqual(call.source, "llm")

    def test_tool_result_states(self):
        ok = ToolResult.success("echo", "hi")
        self.assertTrue(ok.ok)
        self.assertEqual(ok.status, ToolStatus.SUCCESS)
        fail = ToolResult.failure("echo", "nope")
        self.assertFalse(fail.ok)
        self.assertEqual(fail.status, ToolStatus.FAILURE)
        self.assertEqual(fail.error, "nope")
        denied = ToolResult("c1", "echo", status=ToolStatus.DENIED)
        self.assertEqual(denied.status.value, "denied")


class TestRegistry(unittest.TestCase):
    def _registry(self):
        registry = ToolRegistry()
        registry.register(EchoTool())
        registry.register(GetCurrentTimeTool())
        return registry

    def test_register_get_list(self):
        registry = self._registry()
        self.assertIn("echo", registry)
        self.assertIsInstance(registry.get("echo"), EchoTool)
        names = [t.name for t in registry.list()]
        self.assertIn("echo", names)
        self.assertIn("get_current_time", names)

    def test_duplicate_registration_raises(self):
        registry = self._registry()
        with self.assertRaises(DuplicateToolError):
            registry.register(EchoTool())
        registry.register(EchoTool(), replace=True)  # replace is allowed

    def test_unknown_tool(self):
        registry = self._registry()
        self.assertIsNone(registry.get("nope"))
        with self.assertRaises(UnknownToolError):
            registry.require("nope")

    def test_remove(self):
        registry = self._registry()
        removed = registry.remove("echo")
        self.assertIsInstance(removed, EchoTool)
        self.assertIsNone(registry.get("echo"))
        self.assertIsNone(registry.remove("nope"))

    def test_disabled_tool_excluded_from_definitions(self):
        registry = self._registry()
        tool = registry.get("echo")
        tool.enabled = False
        names = [d.name for d in registry.definitions()]
        self.assertNotIn("echo", names)
        self.assertFalse(registry.is_enabled("echo"))


class TestValidation(unittest.TestCase):
    SCHEMA = {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "count": {"type": "integer"},
            "mode": {"type": "string", "enum": ["a", "b"]},
        },
        "required": ["text"],
        "additionalProperties": False,
    }

    def test_valid(self):
        result = validate_arguments(self.SCHEMA, {"text": "hi", "count": 3, "mode": "a"})
        self.assertTrue(result.valid)
        self.assertEqual(result.errors, [])

    def test_missing_required(self):
        result = validate_arguments(self.SCHEMA, {})
        self.assertFalse(result.valid)
        self.assertIn("missing required argument: text", result.errors)

    def test_wrong_type(self):
        result = validate_arguments(self.SCHEMA, {"text": "hi", "count": "x"})
        self.assertFalse(result.valid)
        self.assertTrue(any("must be integer" in e for e in result.errors))

    def test_unexpected_argument(self):
        result = validate_arguments(self.SCHEMA, {"text": "hi", "extra": 1})
        self.assertFalse(result.valid)
        self.assertTrue(any("unexpected argument: extra" in e for e in result.errors))

    def test_invalid_enum(self):
        result = validate_arguments(self.SCHEMA, {"text": "hi", "mode": "z"})
        self.assertFalse(result.valid)
        self.assertTrue(any("must be one of" in e for e in result.errors))

    def test_malformed_arguments(self):
        result = validate_arguments(self.SCHEMA, "not-a-dict")
        self.assertFalse(result.valid)
        self.assertTrue(any("JSON object" in e for e in result.errors))

    def test_no_schema_allows_anything(self):
        result = validate_arguments(None, {"anything": 1})
        self.assertTrue(result.valid)


class TestExecutor(unittest.TestCase):
    def _executor(self, registry=None, **kwargs):
        registry = registry or ToolRegistry()
        if len(registry) == 0:
            registry.register(EchoTool())
            registry.register(GetCurrentTimeTool())
        return ToolExecutor(registry, bus=kwargs.pop("bus", EventBus()), **kwargs)

    def test_success(self):
        executor = self._executor()
        result = executor.execute(ToolCall.new("echo", {"text": "hi"}), _context())
        self.assertTrue(result.ok)
        self.assertEqual(result.content, "hi")
        self.assertGreater(result.duration_seconds, 0)

    def test_unknown_tool_failure(self):
        executor = self._executor()
        result = executor.execute(ToolCall.new("nope", {}), _context())
        self.assertEqual(result.status, ToolStatus.FAILURE)
        self.assertIn("unknown tool", result.error)

    def test_exception_becomes_failure(self):
        registry = ToolRegistry()
        registry.register(_FailTool())
        executor = self._executor(registry=registry)
        result = executor.execute(ToolCall.new("fail", {}), _context())
        self.assertEqual(result.status, ToolStatus.FAILURE)
        self.assertIn("boom", result.error)

    def test_timeout(self):
        registry = ToolRegistry()
        registry.register(_HangTool())
        executor = ToolExecutor(registry, bus=EventBus(), default_timeout=0.05)
        result = executor.execute(ToolCall.new("hang", {}), _context())
        self.assertEqual(result.status, ToolStatus.TIMEOUT)
        self.assertIn("timed out", result.error)

    def test_cancelled(self):
        registry = ToolRegistry()
        registry.register(_BlockingTool())
        executor = ToolExecutor(registry, bus=EventBus(), default_timeout=5.0)
        cancel = threading.Event()
        cancel.set()
        result = executor.execute(
            ToolCall.new("blocking", {}), _context(cancel_event=cancel)
        )
        self.assertEqual(result.status, ToolStatus.CANCELLED)

    def test_validation_failure(self):
        executor = self._executor()
        result = executor.execute(ToolCall.new("echo", {}), _context())
        self.assertEqual(result.status, ToolStatus.FAILURE)
        self.assertTrue(any("missing required" in e for e in result.metadata.get("validation", [])))

    def test_denied_by_permission(self):
        registry = ToolRegistry()
        registry.register(_HighRiskTool())
        executor = ToolExecutor(registry, bus=EventBus())
        result = executor.execute(ToolCall.new("dangerous_echo", {"text": "x"}), _context())
        self.assertEqual(result.status, ToolStatus.DENIED)
        self.assertIn("risk level", result.error)

    def test_allowlisted_high_risk_runs(self):
        registry = ToolRegistry()
        registry.register(_HighRiskTool())
        policy = DefaultPermissionPolicy(allowlist_names=["dangerous_echo"])
        executor = ToolExecutor(registry, bus=EventBus(), policy=policy)
        result = executor.execute(ToolCall.new("dangerous_echo", {"text": "x"}), _context())
        self.assertTrue(result.ok)

    def test_disabled_tool_denied(self):
        registry = ToolRegistry()
        registry.register(EchoTool())
        registry.get("echo").enabled = False
        executor = ToolExecutor(registry, bus=EventBus())
        result = executor.execute(ToolCall.new("echo", {"text": "x"}), _context())
        self.assertEqual(result.status, ToolStatus.DENIED)

    def test_events_published(self):
        bus = EventBus()
        seen = []
        bus.subscribe(seen.append, EventType.TOOL_CALL_STARTED)
        bus.subscribe(seen.append, EventType.TOOL_CALL_COMPLETED)
        executor = self._executor(bus=bus)
        executor.execute(ToolCall.new("echo", {"text": "hi"}), _context())
        types = [e.type for e in seen]
        self.assertIn(EventType.TOOL_CALL_STARTED, types)
        self.assertIn(EventType.TOOL_CALL_COMPLETED, types)

    def test_failure_event_published(self):
        bus = EventBus()
        seen = []
        bus.subscribe(seen.append, EventType.TOOL_CALL_FAILED)
        executor = self._executor(bus=bus)
        executor.execute(ToolCall.new("nope", {}), _context())
        self.assertEqual(seen[-1].type, EventType.TOOL_CALL_FAILED)

    def test_temporal_activity_recorded(self):
        bus = EventBus()
        temporal = mock.Mock()
        executor = self._executor(bus=bus, temporal=temporal)
        executor.execute(ToolCall.new("echo", {"text": "hi"}), _context())
        statuses = [c.args[1] for c in temporal.record_tool_activity.call_args_list]
        self.assertIn("STARTED", statuses)
        self.assertIn("COMPLETED", statuses)


class TestPermissionPolicy(unittest.TestCase):
    def test_default_allows_low_risk(self):
        policy = DefaultPermissionPolicy()
        decision = policy.check(EchoTool().definition(), _context())
        self.assertIsInstance(decision, PermissionDecision)
        self.assertTrue(decision.allowed)

    def test_default_denies_critical(self):
        policy = DefaultPermissionPolicy()
        decision = policy.check(_HighRiskTool().definition(), _context())
        self.assertFalse(decision.allowed)

    def test_allowlist_overrides(self):
        policy = DefaultPermissionPolicy(allowlist_names=["dangerous_echo"])
        decision = policy.check(_HighRiskTool().definition(), _context())
        self.assertTrue(decision.allowed)


class TestAdapter(unittest.TestCase):
    def test_to_tool_calls_converts_vendor_neutral(self):
        response = LLMResponse(
            content="",
            tool_calls=[
                {"id": "c1", "name": "echo", "arguments": {"text": "hi"}},
                {"id": "c2", "name": "get_current_time", "arguments": '{"format":"iso"}'},
            ],
        )
        calls = ToolCallAdapter.to_tool_calls(response)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].call_id, "c1")
        self.assertEqual(calls[0].tool_name, "echo")
        self.assertEqual(calls[0].arguments, {"text": "hi"})
        self.assertEqual(calls[1].arguments, {"format": "iso"})

    def test_to_tool_calls_handles_malformed_json(self):
        response = LLMResponse(tool_calls=[{"id": "x", "name": "echo", "arguments": "{bad"}])
        calls = ToolCallAdapter.to_tool_calls(response)
        self.assertEqual(len(calls), 1)
        self.assertIn("_raw", calls[0].arguments)

    def test_to_provider_tools(self):
        definitions = [EchoTool().definition(), GetCurrentTimeTool().definition()]
        tools = ToolCallAdapter.to_provider_tools(definitions)
        self.assertEqual(len(tools), 2)
        self.assertEqual(tools[0]["type"], "function")
        self.assertEqual(tools[0]["function"]["name"], "echo")


class TestProviderAdapter(unittest.TestCase):
    """LLM Provider -> internal ToolCall: verify the real OpenAI-compatible
    provider sends the tools payload and parses vendor tool_calls back."""

    def _provider(self):
        from chris.llm.openai_compat import OpenAICompatProvider

        return OpenAICompatProvider(
            base_url="https://example.com/v1", api_key="test-key", model="test-model"
        )

    def test_generate_with_tools_sends_tools_payload(self):
        from tests.test_openai_compat import FakeResponse, _chat_response

        provider = self._provider()
        tools = [
            {"type": "function", "function": {"name": "echo", "parameters": {}}}
        ]
        with mock.patch(
            "chris.llm.openai_compat.requests.post",
            return_value=FakeResponse(data=_chat_response("ok")),
        ) as post:
            response = provider.generate_with_tools(
                [{"role": "user", "content": "hi"}], tools=tools
            )
        self.assertEqual(response.content, "ok")
        self.assertEqual(response.tool_calls, [])
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["tools"], tools)

    def test_generate_with_tools_parses_vendor_tool_calls(self):
        from tests.test_openai_compat import FakeResponse

        vendor = {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "echo",
                                    "arguments": '{"text": "hi"}',
                                },
                            }
                        ],
                    }
                }
            ]
        }
        with mock.patch(
            "chris.llm.openai_compat.requests.post",
            return_value=FakeResponse(data=vendor),
        ):
            response = self._provider().generate_with_tools([], tools=None)
        self.assertEqual(len(response.tool_calls), 1)
        self.assertEqual(response.tool_calls[0]["id"], "call_1")
        self.assertEqual(response.tool_calls[0]["name"], "echo")
        self.assertEqual(response.tool_calls[0]["arguments"], '{"text": "hi"}')

    def test_vendor_tool_calls_flow_into_internal_tool_call(self):
        from tests.test_openai_compat import FakeResponse

        vendor = {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_9",
                                "type": "function",
                                "function": {
                                    "name": "get_current_time",
                                    "arguments": '{"format": "natural"}',
                                },
                            }
                        ],
                    }
                }
            ]
        }
        with mock.patch(
            "chris.llm.openai_compat.requests.post",
            return_value=FakeResponse(data=vendor),
        ):
            response = self._provider().generate_with_tools([], tools=None)
        calls = ToolCallAdapter.to_tool_calls(response)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].call_id, "call_9")
        self.assertEqual(calls[0].tool_name, "get_current_time")
        self.assertEqual(calls[0].arguments, {"format": "natural"})


class _ToolProvider:
    """Fake provider: first generate_with_tools call returns a tool call, then a final reply."""

    def __init__(self, tool_calls, final_reply="final"):
        self._tool_calls = tool_calls
        self._final = final_reply
        self.saw_tools = None
        self.rounds = 0

    def generate_with_tools(self, messages, tools=None, **kwargs):
        self.rounds += 1
        if self.saw_tools is None:
            self.saw_tools = tools
        if self._tool_calls:
            response = LLMResponse(content="", tool_calls=[self._tool_calls.pop(0)])
            self._tool_calls = []
            return response
        return LLMResponse(content=self._final)

    def generate(self, messages, **kwargs):
        return self._final


class _StubRuntime:
    def __init__(self, registry, executor, provider):
        self.tool_registry = registry
        self.tool_executor = executor
        self.provider = provider
        self.config = Config()
        self.config.tools = {"enabled": True, "timeout": 10.0}
        self.bus = EventBus()
        self.temporal = mock.Mock()
        self.temporal.now.return_value = datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc)
        self.temporal.session_active.return_value = True
        self.memory = mock.Mock()
        self.memory.list_recent.return_value = []
        self.world = mock.Mock()
        self.world.location = "home"
        self.world.current_activity = "idle"
        self.world.user_presence = True


class TestPipeline(unittest.TestCase):
    def _runtime(self, provider=None, tools_enabled=True):
        registry = ToolRegistry()
        registry.register(EchoTool())
        registry.register(GetCurrentTimeTool())
        executor = ToolExecutor(registry, bus=EventBus())
        provider = provider or _ToolProvider([{"id": "t1", "name": "echo", "arguments": {"text": "hi"}}])
        runtime = _StubRuntime(registry, executor, provider)
        runtime.config.tools = {"enabled": tools_enabled, "timeout": 10.0}
        return runtime

    def test_pipeline_runs_tool_and_returns_final(self):
        runtime = self._runtime()
        reply = run_tool_pipeline(runtime, [{"role": "user", "content": "say hi"}])
        self.assertEqual(reply, "final")
        self.assertEqual(runtime.provider.rounds, 2)
        self.assertIsNotNone(runtime.provider.saw_tools)
        self.assertEqual(runtime.provider.saw_tools[0]["function"]["name"], "echo")

    def test_pipeline_returns_none_when_disabled(self):
        runtime = self._runtime(tools_enabled=False)
        self.assertIsNone(run_tool_pipeline(runtime, [{"role": "user", "content": "x"}]))

    def test_pipeline_returns_none_when_no_tools_registered(self):
        runtime = self._runtime()
        runtime.tool_registry = ToolRegistry()  # empty
        self.assertIsNone(run_tool_pipeline(runtime, [{"role": "user", "content": "x"}]))

    def test_pipeline_returns_none_without_provider_support(self):
        class _PlainProvider:
            def generate(self, messages, **kwargs):
                return "ok"

        runtime = self._runtime(provider=_PlainProvider())
        self.assertIsNone(run_tool_pipeline(runtime, [{"role": "user", "content": "x"}]))

    def test_pipeline_no_tool_call_returns_content(self):
        provider = _ToolProvider([], final_reply="plain reply")
        runtime = self._runtime(provider=provider)
        reply = run_tool_pipeline(runtime, [{"role": "user", "content": "hi"}])
        self.assertEqual(reply, "plain reply")


class TestBuiltinTools(unittest.TestCase):
    def test_get_current_time_iso(self):
        tool = GetCurrentTimeTool()
        context = _context()
        result = tool.execute(context, {})
        self.assertTrue(result.ok)
        self.assertEqual(result.content, context.now.isoformat())

    def test_get_current_time_natural(self):
        tool = GetCurrentTimeTool()
        result = tool.execute(_context(), {"format": "natural"})
        self.assertEqual(result.content, "2026-08-12 10:00")

    def test_echo(self):
        tool = EchoTool()
        result = tool.execute(_context(), {"text": "hello"})
        self.assertEqual(result.content, "hello")
        self.assertEqual(result.metadata, {"echoed": True})


if __name__ == "__main__":
    unittest.main()
