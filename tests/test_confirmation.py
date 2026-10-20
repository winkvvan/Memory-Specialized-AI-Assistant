"""Phase 8.6 Permission Confirmation tests (offline).

Covers: ConfirmationRequest model, ConfirmationManager state machine (create /
approve / deny / cancel / expire / consume, invalid transitions, TTL limits,
concurrency), ToolExecutor integration (confirmation_required + execute_approved
re-gate), EventBus, Temporal, sensitive-data handling, Computer/File/Coding
integration, CRITICAL still denied, and full E2E flows with fake LLM + fake
backends.
"""

import threading
import time
import unittest
from datetime import datetime, timezone
from unittest import mock

from chris.config import Config
from chris.events import EventBus, EventType
from chris.llm.base import LLMResponse
from chris.tools import ToolCall, ToolContext
from chris.tools.confirmation.errors import ConfirmationError, ConfirmationErrorCode
from chris.tools.confirmation.manager import ConfirmationManager
from chris.tools.confirmation.models import ConfirmationRequest, ConfirmationStatus
from chris.tools.confirmation.reason import render_reason
from chris.tools.executor import ToolExecutor
from chris.tools.permission import DefaultPermissionPolicy, PermissionOutcome
from chris.tools.registry import ToolRegistry


def _context(**kwargs):
    return ToolContext(now=datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc), **kwargs)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TestConfirmationModel(unittest.TestCase):
    def test_default_status_pending(self):
        request = ConfirmationRequest(call_id="c1", tool_name="t")
        self.assertEqual(request.status, ConfirmationStatus.PENDING)
        self.assertTrue(request.confirmation_id)

    def test_to_dict_and_safe_summary(self):
        request = ConfirmationRequest(
            call_id="c1", tool_name="computer_type_text",
            arguments={"text": "secret password"}, risk_level="MEDIUM",
        )
        self.assertEqual(request.to_dict()["status"], "PENDING")
        summary = request.safe_summary()
        self.assertNotIn("secret", str(summary))
        self.assertNotIn("arguments", summary)

    def test_is_expired(self):
        request = ConfirmationRequest(call_id="c1", tool_name="t")
        request.expires_at = datetime.now(timezone.utc)
        self.assertTrue(request.is_expired())


# ---------------------------------------------------------------------------
# Manager state machine
# ---------------------------------------------------------------------------


class TestConfirmationManager(unittest.TestCase):
    def _manager(self, ttl=60.0, max_ttl=300.0):
        return ConfirmationManager(bus=EventBus(), default_ttl_seconds=ttl, max_ttl_seconds=max_ttl)

    def _create(self, manager, tool_name="computer_click", **kwargs):
        return manager.create_request(
            call_id="call-1", tool_name=tool_name,
            arguments=kwargs.get("arguments", {"x": 1, "y": 2}),
            risk_level="MEDIUM", reason="test confirmation",
        )

    def test_create_and_get(self):
        manager = self._manager()
        request = self._create(manager)
        self.assertIs(manager.get_request(request.confirmation_id), request)

    def test_approve_then_consume(self):
        manager = self._manager()
        request = self._create(manager)
        approved = manager.approve(request.confirmation_id)
        self.assertEqual(approved.status, ConfirmationStatus.APPROVED)
        consumed = manager.consume(request.confirmation_id)
        self.assertEqual(consumed.status, ConfirmationStatus.CONSUMED)

    def test_deny(self):
        manager = self._manager()
        request = self._create(manager)
        denied = manager.deny(request.confirmation_id)
        self.assertEqual(denied.status, ConfirmationStatus.DENIED)

    def test_cancel(self):
        manager = self._manager()
        request = self._create(manager)
        cancelled = manager.cancel(request.confirmation_id)
        self.assertEqual(cancelled.status, ConfirmationStatus.CANCELLED)

    def test_consume_without_approve_is_invalid(self):
        manager = self._manager()
        request = self._create(manager)
        with self.assertRaises(ConfirmationError) as cm:
            manager.consume(request.confirmation_id)
        self.assertEqual(cm.exception.code, ConfirmationErrorCode.CONFIRMATION_INVALID_TRANSITION)

    def test_duplicate_approve_is_invalid(self):
        manager = self._manager()
        request = self._create(manager)
        manager.approve(request.confirmation_id)
        with self.assertRaises(ConfirmationError) as cm:
            manager.approve(request.confirmation_id)
        self.assertEqual(cm.exception.code, ConfirmationErrorCode.CONFIRMATION_INVALID_TRANSITION)

    def test_approve_after_deny_is_invalid(self):
        manager = self._manager()
        request = self._create(manager)
        manager.deny(request.confirmation_id)
        with self.assertRaises(ConfirmationError) as cm:
            manager.approve(request.confirmation_id)
        self.assertEqual(cm.exception.code, ConfirmationErrorCode.CONFIRMATION_INVALID_TRANSITION)

    def test_approve_after_cancel_is_invalid(self):
        manager = self._manager()
        request = self._create(manager)
        manager.cancel(request.confirmation_id)
        with self.assertRaises(ConfirmationError) as cm:
            manager.approve(request.confirmation_id)
        self.assertEqual(cm.exception.code, ConfirmationErrorCode.CONFIRMATION_INVALID_TRANSITION)

    def test_consume_twice_is_invalid(self):
        manager = self._manager()
        request = self._create(manager)
        manager.approve(request.confirmation_id)
        manager.consume(request.confirmation_id)
        with self.assertRaises(ConfirmationError) as cm:
            manager.consume(request.confirmation_id)
        self.assertEqual(cm.exception.code, ConfirmationErrorCode.CONFIRMATION_INVALID_TRANSITION)

    def test_unknown_confirmation(self):
        manager = self._manager()
        with self.assertRaises(ConfirmationError) as cm:
            manager.approve("nope")
        self.assertEqual(cm.exception.code, ConfirmationErrorCode.CONFIRMATION_NOT_FOUND)

    def test_ttl_capped_at_max(self):
        manager = self._manager(max_ttl=10.0)
        request = self._create(manager)
        # default ttl 60 but max is 10 -> capped
        elapsed = (request.expires_at - request.created_at).total_seconds()
        self.assertLessEqual(elapsed, 10.0 + 0.01)

    def test_expire_pending(self):
        # use a fake clock to force expiry
        fake_now = [datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc)]

        def clock():
            return fake_now[0]

        manager = ConfirmationManager(bus=EventBus(), clock=clock, default_ttl_seconds=60.0)
        request = self._create(manager)
        fake_now[0] = datetime(2026, 8, 12, 10, 5, tzinfo=timezone.utc)  # +5 min
        expired = manager.expire_pending()
        self.assertEqual(len(expired), 1)
        self.assertEqual(expired[0].status, ConfirmationStatus.EXPIRED)

    def test_approve_after_expired(self):
        fake_now = [datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc)]
        manager = ConfirmationManager(bus=EventBus(), clock=lambda: fake_now[0])
        request = self._create(manager)
        fake_now[0] = datetime(2026, 8, 12, 10, 5, tzinfo=timezone.utc)
        with self.assertRaises(ConfirmationError) as cm:
            manager.approve(request.confirmation_id)
        self.assertEqual(cm.exception.code, ConfirmationErrorCode.CONFIRMATION_EXPIRED)

    def test_concurrent_confirmations_independent(self):
        manager = self._manager()
        a = manager.create_request(call_id="a", tool_name="click", arguments={}, risk_level="MEDIUM", reason="A")
        b = manager.create_request(call_id="b", tool_name="type_text", arguments={}, risk_level="MEDIUM", reason="B")
        manager.approve(a.confirmation_id)
        self.assertEqual(a.status, ConfirmationStatus.APPROVED)
        self.assertEqual(b.status, ConfirmationStatus.PENDING)
        manager.deny(b.confirmation_id)
        self.assertEqual(a.status, ConfirmationStatus.APPROVED)
        self.assertEqual(b.status, ConfirmationStatus.DENIED)

    def test_id_uniqueness(self):
        manager = self._manager()
        a = self._create(manager)
        b = self._create(manager)
        self.assertNotEqual(a.confirmation_id, b.confirmation_id)

    def test_list_pending(self):
        manager = self._manager()
        a = self._create(manager)
        b = self._create(manager)
        self.assertEqual(len(manager.list_pending()), 2)
        manager.approve(a.confirmation_id)
        self.assertEqual(len(manager.list_pending()), 1)

    def test_events_emitted(self):
        bus = EventBus()
        seen = []
        bus.subscribe(seen.append, [
            EventType.CONFIRMATION_REQUESTED,
            EventType.CONFIRMATION_APPROVED,
            EventType.CONFIRMATION_CONSUMED,
        ])
        manager = ConfirmationManager(bus=bus)
        request = self._create(manager)
        manager.approve(request.confirmation_id)
        manager.consume(request.confirmation_id)
        types = [e.type for e in seen]
        self.assertIn(EventType.CONFIRMATION_REQUESTED, types)
        self.assertIn(EventType.CONFIRMATION_APPROVED, types)
        self.assertIn(EventType.CONFIRMATION_CONSUMED, types)

    def test_event_payload_has_no_arguments(self):
        bus = EventBus()
        seen = []
        bus.subscribe(seen.append, EventType.CONFIRMATION_REQUESTED)
        manager = ConfirmationManager(bus=bus)
        self._create(manager, arguments={"text": "my-password"})
        payload = seen[0].payload
        self.assertNotIn("arguments", payload)
        self.assertNotIn("my-password", str(payload))

    def test_temporal_recorded(self):
        temporal = mock.Mock()
        manager = ConfirmationManager(bus=EventBus(), temporal=temporal)
        request = self._create(manager)
        manager.approve(request.confirmation_id)
        statuses = [c.args[1] for c in temporal.record_tool_activity.call_args_list]
        self.assertIn("REQUESTED", statuses)
        self.assertIn("APPROVED", statuses)


class TestRenderReason(unittest.TestCase):
    def test_sensitive_text_masked(self):
        request = ConfirmationRequest(
            call_id="c1", tool_name="computer_type_text",
            arguments={"text": "my-secret-123"}, risk_level="MEDIUM", reason="r",
        )
        text = render_reason(request)
        self.assertNotIn("my-secret-123", text)
        self.assertIn("chars", text)

    def test_coordinates_visible(self):
        request = ConfirmationRequest(
            call_id="c1", tool_name="computer_click",
            arguments={"x": 812, "y": 431}, risk_level="MEDIUM", reason="r",
        )
        text = render_reason(request)
        self.assertIn("812", text)
        self.assertIn("431", text)


# ---------------------------------------------------------------------------
# Permission tri-state
# ---------------------------------------------------------------------------


class TestPermissionTriState(unittest.TestCase):
    def test_readonly_allow(self):
        from chris.tools import RiskLevel
        from chris.tools.models import ToolDefinition

        policy = DefaultPermissionPolicy()
        decision = policy.check(ToolDefinition(name="x", description="d", risk_level=RiskLevel.READ_ONLY), _context())
        self.assertEqual(decision.outcome, PermissionOutcome.ALLOW)

    def test_medium_requires_confirmation(self):
        from chris.tools import RiskLevel
        from chris.tools.models import ToolDefinition

        policy = DefaultPermissionPolicy()
        decision = policy.check(ToolDefinition(name="x", description="d", risk_level=RiskLevel.MEDIUM), _context())
        self.assertEqual(decision.outcome, PermissionOutcome.CONFIRMATION_REQUIRED)
        self.assertTrue(decision.confirmation_required)

    def test_high_requires_confirmation(self):
        from chris.tools import RiskLevel
        from chris.tools.models import ToolDefinition

        policy = DefaultPermissionPolicy()
        decision = policy.check(ToolDefinition(name="x", description="d", risk_level=RiskLevel.HIGH), _context())
        self.assertEqual(decision.outcome, PermissionOutcome.CONFIRMATION_REQUIRED)

    def test_critical_denied(self):
        from chris.tools import RiskLevel
        from chris.tools.models import ToolDefinition

        policy = DefaultPermissionPolicy()
        decision = policy.check(ToolDefinition(name="x", description="d", risk_level=RiskLevel.CRITICAL), _context())
        self.assertEqual(decision.outcome, PermissionOutcome.DENY)
        self.assertFalse(decision.confirmation_required)

    def test_allowlist_overrides(self):
        from chris.tools import RiskLevel
        from chris.tools.models import ToolDefinition

        policy = DefaultPermissionPolicy(allowlist_names=["x"])
        decision = policy.check(ToolDefinition(name="x", description="d", risk_level=RiskLevel.MEDIUM), _context())
        self.assertEqual(decision.outcome, PermissionOutcome.ALLOW)


# ---------------------------------------------------------------------------
# ToolExecutor integration
# ---------------------------------------------------------------------------


class _EchoTool:
    name = "risky_echo"
    description = "test"
    permission = "MEDIUM"
    risk_level = "MEDIUM"
    input_schema = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}

    def __init__(self):
        self.executed = []
        self.enabled = True

    def definition(self):
        from chris.tools import RiskLevel
        from chris.tools.models import ToolDefinition

        return ToolDefinition(name=self.name, description=self.description,
                              input_schema=self.input_schema, enabled=self.enabled,
                              permission=RiskLevel.MEDIUM, risk_level=RiskLevel.MEDIUM)

    def execute(self, context, arguments):
        self.executed.append(dict(arguments))
        from chris.tools import ToolResult

        return ToolResult(tool_name=self.name, content="echoed " + arguments.get("text", ""))


class TestExecutorIntegration(unittest.TestCase):
    def _setup(self):
        registry = ToolRegistry()
        tool = _EchoTool()
        registry.register(tool)
        bus = EventBus()
        manager = ConfirmationManager(bus=bus)
        executor = ToolExecutor(registry, bus=bus, policy=DefaultPermissionPolicy(), confirmation=manager)
        return registry, tool, manager, executor

    def test_confirmation_required_returns_structured(self):
        registry, tool, manager, executor = self._setup()
        call = ToolCall.new("risky_echo", {"text": "hi"})
        result = executor.execute(call, _context())
        from chris.tools import ToolStatus

        self.assertEqual(result.status, ToolStatus.DENIED)
        self.assertEqual(result.metadata["error_code"], "confirmation_required")
        self.assertIn("confirmation_id", result.metadata)
        self.assertIn("expires_at", result.metadata)
        # tool not executed
        self.assertEqual(tool.executed, [])

    def test_approve_and_execute(self):
        registry, tool, manager, executor = self._setup()
        call = ToolCall.new("risky_echo", {"text": "hi"})
        first = executor.execute(call, _context())
        confirmation_id = first.metadata["confirmation_id"]
        result = executor.execute_approved(call, _context(), confirmation_id)
        from chris.tools import ToolStatus

        self.assertEqual(result.status, ToolStatus.SUCCESS)
        self.assertEqual(result.content, "echoed hi")
        self.assertEqual(tool.executed, [{"text": "hi"}])

    def test_approve_consumes_one_shot(self):
        registry, tool, manager, executor = self._setup()
        call = ToolCall.new("risky_echo", {"text": "hi"})
        first = executor.execute(call, _context())
        cid = first.metadata["confirmation_id"]
        executor.execute_approved(call, _context(), cid)
        # second approval should fail (already consumed)
        result = executor.execute_approved(call, _context(), cid)
        from chris.tools import ToolStatus

        self.assertEqual(result.status, ToolStatus.DENIED)
        self.assertEqual(result.metadata["error_code"], "confirmation_invalid_transition")

    def test_approve_wrong_call_mismatch(self):
        registry, tool, manager, executor = self._setup()
        call = ToolCall.new("risky_echo", {"text": "hi"})
        first = executor.execute(call, _context())
        cid = first.metadata["confirmation_id"]
        # different arguments -> mismatch
        other = ToolCall.new("risky_echo", {"text": "DIFFERENT"})
        result = executor.execute_approved(other, _context(), cid)
        from chris.tools import ToolStatus

        self.assertEqual(result.status, ToolStatus.DENIED)

    def test_post_approval_validation_still_enforced(self):
        registry, tool, manager, executor = self._setup()
        call = ToolCall.new("risky_echo", {"text": "hi"})
        first = executor.execute(call, _context())
        cid = first.metadata["confirmation_id"]
        # arguments were validated at creation; disable tool then approve
        tool.enabled = False
        result = executor.execute_approved(call, _context(), cid)
        from chris.tools import ToolStatus

        self.assertEqual(result.status, ToolStatus.DENIED)

    def test_critical_never_confirmable(self):
        from chris.tools import RiskLevel
        from chris.tools.models import ToolDefinition

        class _CriticalTool(_EchoTool):
            name = "critical_op"
            risk_level = RiskLevel.CRITICAL
            permission = RiskLevel.CRITICAL

            def definition(self):
                return ToolDefinition(name=self.name, description=self.description,
                                      input_schema=self.input_schema, enabled=True,
                                      permission=RiskLevel.CRITICAL, risk_level=RiskLevel.CRITICAL)

        registry = ToolRegistry()
        tool = _CriticalTool()
        registry.register(tool)
        manager = ConfirmationManager(bus=EventBus())
        executor = ToolExecutor(registry, bus=EventBus(), policy=DefaultPermissionPolicy(), confirmation=manager)
        call = ToolCall.new("critical_op", {"text": "x"})
        result = executor.execute(call, _context())
        from chris.tools import ToolStatus

        self.assertEqual(result.status, ToolStatus.DENIED)
        self.assertEqual(result.metadata["error_code"], "permission_denied")
        # no pending confirmation was created
        self.assertEqual(len(manager.list_pending()), 0)

    def test_no_confirmation_manager_falls_back_to_denied(self):
        registry = ToolRegistry()
        tool = _EchoTool()
        registry.register(tool)
        executor = ToolExecutor(registry, bus=EventBus(), policy=DefaultPermissionPolicy())
        call = ToolCall.new("risky_echo", {"text": "hi"})
        result = executor.execute(call, _context())
        from chris.tools import ToolStatus

        self.assertEqual(result.status, ToolStatus.DENIED)
        self.assertEqual(result.metadata["error_code"], "confirmation_required")
        self.assertNotIn("confirmation_id", result.metadata)


class TestCLIAnswerInterpretation(unittest.TestCase):
    def test_explicit_yes_approves(self):
        from chris.cli import interpret_confirmation_answer

        for word in ("y", "yes", "允许", "确认", "批准", "同意", "YES", "Yes"):
            self.assertTrue(interpret_confirmation_answer(word), word)

    def test_explicit_no_denies(self):
        from chris.cli import interpret_confirmation_answer

        for word in ("n", "no", "拒绝", "取消", "不允许"):
            self.assertFalse(interpret_confirmation_answer(word), word)

    def test_ambiguous_input_never_approves(self):
        from chris.cli import interpret_confirmation_answer

        for word in ("嗯", "应该可以", "你看着办", "maybe", "ok吧", "随便", "", None):
            self.assertFalse(interpret_confirmation_answer(word), repr(word))


# ---------------------------------------------------------------------------
# E2E with fake LLM + pipeline
# ---------------------------------------------------------------------------


class _FakeTool:
    name = "computer_click"
    description = "click"
    permission = "MEDIUM"
    risk_level = "MEDIUM"
    input_schema = {"type": "object", "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}}, "required": ["x", "y"]}

    def __init__(self, manager=None):
        self.manager = manager
        self.executed = []
        self.enabled = True

    def definition(self):
        from chris.tools import RiskLevel
        from chris.tools.models import ToolDefinition

        return ToolDefinition(name=self.name, description=self.description,
                              input_schema=self.input_schema, enabled=True,
                              permission=RiskLevel.MEDIUM, risk_level=RiskLevel.MEDIUM)

    def execute(self, context, arguments):
        self.executed.append(dict(arguments))
        from chris.tools import ToolResult

        return ToolResult(tool_name=self.name, content=f"clicked {arguments['x']},{arguments['y']}")


class _ClickLLM:
    def __init__(self):
        self.rounds = 0
        self.final = "已完成点击。"

    def generate_with_tools(self, messages, tools=None, **kwargs):
        self.rounds += 1
        if self.rounds == 1:
            return LLMResponse(content="", tool_calls=[{"id": "c1", "name": "computer_click", "arguments": {"x": 500, "y": 300}}])
        return LLMResponse(content=self.final)

    def generate(self, messages, **kwargs):
        return self.final


class _StubRuntime:
    def __init__(self, registry, executor, bus):
        self.tool_registry = registry
        self.tool_executor = executor
        self.config = Config()
        self.config.tools = {"enabled": True, "timeout": 30.0}
        self.bus = bus
        self.temporal = mock.Mock()
        self.memory = mock.Mock()
        self.memory.list_recent.return_value = []
        self.world = mock.Mock()
        self.world.location = "home"
        self.world.current_activity = "idle"
        self.world.user_presence = True


class TestE2E(unittest.TestCase):
    def test_click_approve_flow(self):
        from chris.tools.pipeline import run_tool_pipeline

        registry = ToolRegistry()
        tool = _FakeTool()
        registry.register(tool)
        bus = EventBus()
        manager = ConfirmationManager(bus=bus)
        executor = ToolExecutor(registry, bus=bus, policy=DefaultPermissionPolicy(), confirmation=manager)
        runtime = _StubRuntime(registry, executor, bus)
        runtime.provider = _ClickLLM()

        approved = []
        reply = run_tool_pipeline(
            runtime, [{"role": "user", "content": "点击"}],
            confirm=lambda req: (approved.append(req), True)[1],
        )
        self.assertEqual(reply, "已完成点击。")
        self.assertEqual(tool.executed, [{"x": 500, "y": 300}])
        self.assertEqual(len(approved), 1)

    def test_click_deny_flow(self):
        from chris.tools.pipeline import run_tool_pipeline

        registry = ToolRegistry()
        tool = _FakeTool()
        registry.register(tool)
        bus = EventBus()
        manager = ConfirmationManager(bus=bus)
        executor = ToolExecutor(registry, bus=bus, policy=DefaultPermissionPolicy(), confirmation=manager)
        runtime = _StubRuntime(registry, executor, bus)
        runtime.provider = _ClickLLM()

        reply = run_tool_pipeline(
            runtime, [{"role": "user", "content": "点击"}],
            confirm=lambda req: False,
        )
        # tool never executed
        self.assertEqual(tool.executed, [])


if __name__ == "__main__":
    unittest.main()
