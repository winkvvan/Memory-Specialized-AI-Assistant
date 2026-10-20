"""Phase 8.5 Computer Action Core tests (offline; FakeComputerBackend only).

Covers: models, validation (coordinates/text/keys/sequence/timeout), permission
(READ_ONLY/LOW/MEDIUM/HIGH/CRITICAL + allowlist), backend (fake), manager
(success/failure/denied/timeout/cancellation/sequence), tools, Tool Core
integration (Registry->Executor->Permission->Tool->Manager->FakeBackend->
ToolResult->EventBus->Temporal), and an E2E LLM loop.
"""

import threading
import time
import unittest
from datetime import datetime
from unittest import mock

from chris.config import Config
from chris.events import EventBus, EventType
from chris.tools import ToolCall, ToolContext, RiskLevel, ToolStatus
from chris.tools.computer.backend import FakeComputerBackend
from chris.tools.computer.manager import ComputerActionManager
from chris.tools.computer.models import (
    ComputerAction,
    ComputerActionResult,
    ComputerStatus,
)
from chris.tools.computer.policy import ComputerActionPolicy, VALID_KEYS
from chris.tools.computer.tools import build_computer_tools
from chris.tools.executor import ToolExecutor
from chris.tools.permission import DefaultPermissionPolicy
from chris.tools.registry import ToolRegistry


def _context(**kwargs):
    return ToolContext(now=datetime(2026, 8, 12, 10, 0), **kwargs)


def _allow_all():
    return DefaultPermissionPolicy(allowed_risks=list(RiskLevel))


class _Harness:
    def __init__(self, backend=None, policy=None, default_timeout=2.0, max_timeout=5.0):
        self._tmp = None
        self.backend = backend or FakeComputerBackend()
        self.policy = policy or ComputerActionPolicy()
        self.bus = EventBus()
        self.manager = ComputerActionManager(
            backend=self.backend,
            policy=self.policy,
            bus=self.bus,
            default_timeout=default_timeout,
            max_timeout=max_timeout,
            max_actions_per_sequence=5,
        )
        registry = ToolRegistry()
        for tool in build_computer_tools(self.manager):
            registry.register(tool)
        self.registry = registry
        self.executor = ToolExecutor(registry, bus=self.bus, policy=DefaultPermissionPolicy())
        self.executor_all = ToolExecutor(registry, bus=EventBus(), policy=_allow_all())

    def call(self, name, args, executor=None):
        return (executor or self.executor_all).execute(ToolCall.new(name, args), _context())

    def cleanup(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.cleanup()
        return False


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TestModels(unittest.TestCase):
    def test_action_statuses(self):
        for status in ("PENDING", "STARTING", "RUNNING", "COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "DENIED"):
            self.assertTrue(hasattr(ComputerStatus, status))

    def test_action_serialization(self):
        action = ComputerAction(action_type="click", arguments={"x": 1, "y": 2})
        data = action.to_dict()
        self.assertEqual(data["action_type"], "click")
        self.assertEqual(data["arguments"], {"x": 1, "y": 2})
        self.assertEqual(data["status"], "PENDING")

    def test_result_text(self):
        result = ComputerActionResult(status=ComputerStatus.COMPLETED, action_type="click")
        self.assertIn("click", result.to_text())


# ---------------------------------------------------------------------------
# Validation / policy
# ---------------------------------------------------------------------------


class TestPolicyValidation(unittest.TestCase):
    def test_valid_coordinates(self):
        policy = ComputerActionPolicy()
        result = policy.validate_coordinates(100, 200, (1920, 1080))
        self.assertEqual(result, {"x": 100, "y": 200})

    def test_negative_coordinates(self):
        policy = ComputerActionPolicy()
        for coords in ((-1, 5), (5, -1)):
            with self.assertRaises(Exception):
                policy.validate_coordinates(*coords, screen=(1920, 1080))

    def test_out_of_bounds_coordinates(self):
        policy = ComputerActionPolicy()
        with self.assertRaises(Exception):
            policy.validate_coordinates(2000, 100, (1920, 1080))

    def test_malformed_coordinates(self):
        policy = ComputerActionPolicy()
        for coords in (("a", 1), (None, 2), (1, None)):
            with self.assertRaises(Exception):
                policy.validate_coordinates(*coords, screen=(1920, 1080))

    def test_text_too_long(self):
        policy = ComputerActionPolicy()
        with self.assertRaises(Exception):
            policy.validate_text("x" * 100, max_length=50)

    def test_text_not_string(self):
        policy = ComputerActionPolicy()
        with self.assertRaises(Exception):
            policy.validate_text(123, max_length=50)

    def test_invalid_key(self):
        policy = ComputerActionPolicy()
        with self.assertRaises(Exception):
            policy.validate_keys(["not-a-real-key"])

    def test_too_many_keys(self):
        policy = ComputerActionPolicy()
        with self.assertRaises(Exception):
            policy.validate_keys(["a", "b", "c", "d", "e", "f", "g", "h", "i"])

    def test_valid_keys(self):
        policy = ComputerActionPolicy()
        keys = policy.validate_keys(["ctrl", "s"])
        self.assertEqual(keys, ["ctrl", "s"])

    def test_high_risk_hotkey_detected(self):
        policy = ComputerActionPolicy()
        high, _name = policy.hotkey_risk_check(["ctrl", "alt", "delete"])
        self.assertTrue(high)


class TestPermission(unittest.TestCase):
    def test_readonly_allow(self):
        policy = ComputerActionPolicy()
        for action in ("get_screen_size", "get_active_window", "screenshot"):
            allowed, risk, _confirm = policy.decision(action)
            self.assertTrue(allowed, action)
            self.assertEqual(risk, RiskLevel.READ_ONLY)

    def test_low_allow(self):
        policy = ComputerActionPolicy()
        for action in ("move_mouse", "scroll", "wait"):
            allowed, risk, _confirm = policy.decision(action)
            self.assertTrue(allowed, action)
            self.assertEqual(risk, RiskLevel.LOW)

    def test_medium_deny(self):
        policy = ComputerActionPolicy()
        for action in ("click", "double_click", "key_press", "hotkey", "type_text"):
            allowed, risk, confirm = policy.decision(action)
            self.assertFalse(allowed, action)
            self.assertEqual(risk, RiskLevel.MEDIUM)
            self.assertTrue(confirm)

    def test_high_deny(self):
        policy = ComputerActionPolicy()
        allowed, risk, confirm = policy.decision("sequence")
        self.assertFalse(allowed)
        self.assertEqual(risk, RiskLevel.HIGH)
        self.assertTrue(confirm)

    def test_unknown_action_critical(self):
        policy = ComputerActionPolicy()
        allowed, risk, _confirm = policy.decision("execute_command")
        self.assertFalse(allowed)
        self.assertEqual(risk, RiskLevel.CRITICAL)

    def test_allowlist(self):
        policy = ComputerActionPolicy(allowlist_actions=["click"])
        allowed, _risk, confirm = policy.decision("click")
        self.assertTrue(allowed)
        self.assertFalse(confirm)


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------


class TestBackend(unittest.TestCase):
    def test_fake_mouse_and_click(self):
        backend = FakeComputerBackend()
        backend.move_mouse(100, 200)
        backend.click(100, 200)
        actions = [name for name, _ in backend.calls]
        self.assertIn("move_mouse", actions)
        self.assertIn("click", actions)

    def test_fake_screen_and_window(self):
        backend = FakeComputerBackend(screen=(800, 600))
        self.assertEqual(backend.get_screen_size(), {"width": 800, "height": 600})
        window = backend.get_active_window()
        self.assertEqual(window["process_name"], "fake.exe")
        self.assertIsNotNone(window["bounds"])

    def test_fake_type_text_records_length_not_content(self):
        backend = FakeComputerBackend()
        backend.type_text("secret password")
        calls = [c for c in backend.calls if c[0] == "type_text"]
        self.assertEqual(calls[0][1]["text_length"], 15)
        self.assertNotIn("secret", str(calls[0][1]))


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class TestManager(unittest.TestCase):
    def test_success(self):
        with _Harness() as h:
            result = h.manager.run_action("move_mouse", {"x": 10, "y": 20})
            self.assertEqual(result.status, ComputerStatus.COMPLETED)
            self.assertTrue(h.backend.calls)

    def test_failure_unknown_action(self):
        with _Harness() as h:
            # Unknown actions map to CRITICAL risk -> denied (never silently run)
            result = h.manager.run_action("nope", {})
            self.assertEqual(result.status, ComputerStatus.DENIED)

    def test_denied(self):
        with _Harness() as h:
            result = h.manager.run_action("click", {"x": 1, "y": 1})
            self.assertEqual(result.status, ComputerStatus.DENIED)
            self.assertEqual(result.error_code, "confirmation_required")

    def test_timeout(self):
        class _SlowBackend(FakeComputerBackend):
            def wait(self, seconds):
                time.sleep(5)

        with _Harness(backend=_SlowBackend(), default_timeout=0.2, max_timeout=2.0) as h:
            result = h.manager.run_action("wait", {"seconds": 3})
            self.assertEqual(result.status, ComputerStatus.TIMEOUT)

    def test_timeout_limit_exceeded(self):
        with _Harness() as h:
            result = h.manager.run_action("wait", {"seconds": 1}, timeout=999)
            self.assertEqual(result.status, ComputerStatus.FAILED)
            self.assertEqual(result.error_code, "timeout_limit_exceeded")

    def test_sequence_success(self):
        with _Harness() as h:
            result = h.manager.run_sequence(
                [
                    {"type": "move_mouse", "arguments": {"x": 1, "y": 2}},
                    {"type": "wait", "arguments": {"seconds": 0.01}},
                ]
            )
            self.assertEqual(result.status, ComputerStatus.COMPLETED)

    def test_sequence_fails_fast(self):
        with _Harness() as h:
            result = h.manager.run_sequence(
                [
                    {"type": "move_mouse", "arguments": {"x": 1, "y": 2}},
                    {"type": "nope", "arguments": {}},
                    {"type": "move_mouse", "arguments": {"x": 3, "y": 4}},
                ]
            )
            self.assertEqual(result.status, ComputerStatus.FAILED)
            self.assertIn("step 1", result.error)

    def test_sequence_too_long(self):
        with _Harness() as h:
            steps = [{"type": "wait", "arguments": {"seconds": 0.01}}] * 10
            from chris.tools.computer.errors import ComputerError, ComputerErrorCode

            with self.assertRaises(ComputerError) as cm:
                h.manager.run_sequence(steps)
            self.assertEqual(cm.exception.code, ComputerErrorCode.SEQUENCE_TOO_LONG)

    def test_sequence_cancellation(self):
        with _Harness() as h:
            cancel = threading.Event()
            cancel.set()
            result = h.manager.run_sequence(
                [{"type": "wait", "arguments": {"seconds": 0.2}}] * 3,
                cancel_event=cancel,
            )
            self.assertEqual(result.status, ComputerStatus.CANCELLED)

    def test_events_emitted(self):
        with _Harness() as h:
            seen = []
            h.bus.subscribe(seen.append, EventType.COMPUTER_ACTION_STARTED)
            h.bus.subscribe(seen.append, EventType.COMPUTER_ACTION_COMPLETED)
            h.manager.run_action("move_mouse", {"x": 1, "y": 2})
            types = [e.type for e in seen]
            self.assertIn(EventType.COMPUTER_ACTION_STARTED, types)
            self.assertIn(EventType.COMPUTER_ACTION_COMPLETED, types)

    def test_denied_event(self):
        with _Harness() as h:
            seen = []
            h.bus.subscribe(seen.append, EventType.COMPUTER_ACTION_DENIED)
            h.manager.run_action("click", {"x": 1, "y": 1})
            self.assertEqual(seen[-1].type, EventType.COMPUTER_ACTION_DENIED)

    def test_temporal_records_activity(self):
        with _Harness() as h:
            temporal = mock.Mock()
            manager = ComputerActionManager(
                backend=h.backend, policy=h.policy, bus=h.bus, temporal=temporal
            )
            manager.run_action("move_mouse", {"x": 1, "y": 2})
            statuses = [c.args[1] for c in temporal.record_tool_activity.call_args_list]
            self.assertIn("STARTED", statuses)
            self.assertIn("COMPLETED", statuses)

    def test_coordinate_validation_via_manager(self):
        with _Harness() as h:
            result = h.manager.run_action("move_mouse", {"x": 99999, "y": 0})
            self.assertEqual(result.status, ComputerStatus.FAILED)
            self.assertEqual(result.error_code, "invalid_coordinates")


# ---------------------------------------------------------------------------
# Tools + Tool Core integration
# ---------------------------------------------------------------------------


class TestTools(unittest.TestCase):
    def test_screen_info_allowed(self):
        with _Harness() as h:
            result = h.call("computer_screen_info", {}, executor=h.executor)
            self.assertEqual(result.status, ToolStatus.SUCCESS)
            self.assertEqual(result.metadata["status"], "COMPLETED")

    def test_click_denied_by_default(self):
        with _Harness() as h:
            result = h.call("computer_click", {"x": 100, "y": 100}, executor=h.executor)
            self.assertEqual(result.status, ToolStatus.DENIED)
            self.assertEqual(result.metadata["error_code"], "confirmation_required")

    def test_click_allowed_with_allow_all_executor(self):
        with _Harness() as h:
            result = h.call("computer_click", {"x": 100, "y": 100})
            self.assertEqual(result.status, ToolStatus.SUCCESS)

    def test_type_text_success_and_metadata_safe(self):
        with _Harness() as h:
            result = h.call("computer_type_text", {"text": "hello"})
            self.assertEqual(result.status, ToolStatus.SUCCESS)
            # metadata must not contain the text
            self.assertNotIn("hello", str(result.metadata))

    def test_sequence_denied_by_default(self):
        with _Harness() as h:
            result = h.call(
                "computer_sequence",
                {"steps": [{"type": "move_mouse", "arguments": {"x": 1, "y": 2}}]},
                executor=h.executor,
            )
            self.assertEqual(result.status, ToolStatus.DENIED)

    def test_validation_rejects_missing_args(self):
        with _Harness() as h:
            result = h.call("computer_click", {}, executor=h.executor)
            self.assertEqual(result.status, ToolStatus.FAILURE)
            self.assertTrue(any("missing required" in e for e in result.metadata.get("validation", [])))


# ---------------------------------------------------------------------------
# E2E
# ---------------------------------------------------------------------------


class _ComputerLLM:
    def __init__(self):
        self.rounds = 0
        self.final = "已获取屏幕信息。"
        self.last_messages = None

    def generate_with_tools(self, messages, tools=None, **kwargs):
        self.rounds += 1
        self.last_messages = list(messages)
        if self.rounds == 1:
            return __import__("chris.llm.base", fromlist=["LLMResponse"]).LLMResponse(
                content="",
                tool_calls=[
                    {"id": "c1", "name": "computer_screen_info", "arguments": {}}
                ],
            )
        return __import__("chris.llm.base", fromlist=["LLMResponse"]).LLMResponse(
            content=self.final
        )

    def generate(self, messages, **kwargs):
        return self.final


class _StubRuntime:
    def __init__(self, harness):
        self.tool_registry = harness.registry
        self.tool_executor = harness.executor_all
        self.config = Config()
        self.config.tools = {"enabled": True, "timeout": 30.0}
        self.bus = harness.bus
        self.temporal = mock.Mock()
        self.memory = mock.Mock()
        self.memory.list_recent.return_value = []
        self.world = mock.Mock()
        self.world.location = "home"
        self.world.current_activity = "idle"
        self.world.user_presence = True


class TestEndToEnd(unittest.TestCase):
    def test_llm_toolcall_computer_llm(self):
        from chris.tools.pipeline import run_tool_pipeline

        with _Harness() as h:
            runtime = _StubRuntime(h)
            runtime.provider = _ComputerLLM()
            reply = run_tool_pipeline(
                runtime, [{"role": "user", "content": "屏幕多大？"}]
            )
            self.assertEqual(reply, runtime.provider.final)
            self.assertEqual(runtime.provider.rounds, 2)


if __name__ == "__main__":
    unittest.main()
