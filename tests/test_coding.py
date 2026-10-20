"""Phase 8.4 OpenCode / Coding Agent integration tests (offline).

Uses a FakeOpenCodeAdapter and fake LLM - never the real OpenCode binary. Covers:
models, policy (scope risk + env sanitization), adapter (fake process: success /
failure / timeout / cancellation), manager (lifecycle, concurrency, events,
temporal), project resolution (workspace boundary, traversal), parser (changed
files via git, test heuristics), tools (submit/status/cancel), permission
(READ_ONLY/MEDIUM/HIGH/CRITICAL), and the full LLM -> ToolCall -> CodingTaskTool
-> Manager -> FakeOpenCodeAdapter -> CodingResult -> ToolResult -> LLM loop.
"""

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from chris.config import Config
from chris.events import EventBus, EventType
from chris.tools import ToolCall, ToolContext, ToolStatus
from chris.tools.coding.adapter import OpenCodeAdapter
from chris.tools.coding.manager import CodingTaskManager
from chris.tools.coding.models import CodingResult, CodingStatus, ProjectContext
from chris.tools.coding.policy import (
    DEFAULT_ENV_ALLOWLIST,
    CodingExecutionPolicy,
    GIT_WRITE,
    PROJECT_PACKAGE,
    PROJECT_TEST,
    SYSTEM_COMMAND,
)
from chris.tools.coding.project import ProjectResolver
from chris.tools.coding.tool import build_coding_tools
from chris.tools.executor import ToolExecutor
from chris.tools.filesystem.path import WorkspacePathPolicy
from chris.tools.permission import DefaultPermissionPolicy
from chris.tools.registry import ToolRegistry


class FakeOpenCodeAdapter:
    """Fake adapter with a controllable result and optional delay."""

    def __init__(self, result: CodingResult | None = None, delay: float = 0.0):
        self.result = result or CodingResult(
            status=CodingStatus.COMPLETED,
            summary="implemented login",
            changed_files=["src/auth.py", "tests/test_auth.py"],
            tests="PASS",
            exit_code=0,
        )
        self.delay = delay
        self.started = False
        self.cancelled = False
        self.start_args = None
        self.available = True
        self.timeout = None

    def is_available(self):
        return self.available

    def version(self):
        return "fake-1.0"

    def start(self, project_root, prompt, *, extra_args=None):
        self.started = True
        self.start_args = (project_root, prompt, extra_args)

    def wait(self, timeout=None, poll=0.01):
        self.timeout = timeout
        started = time.time()
        while self.delay and (time.time() - started) < self.delay:
            if self.cancelled:
                return CodingResult(
                    status=CodingStatus.CANCELLED,
                    summary="cancelled",
                    error="cancelled",
                    exit_code=None,
                )
            if timeout is not None and (time.time() - started) >= timeout:
                return CodingResult(
                    status=CodingStatus.TIMEOUT,
                    summary=f"timed out after {timeout}s",
                    error=f"timed out after {timeout}s",
                    exit_code=None,
                )
            time.sleep(poll)
        return self.result

    def cancel(self):
        self.cancelled = True


def _context(**kwargs):
    return ToolContext(now=__import__("datetime").datetime(2026, 8, 12, 10, 0), **kwargs)


class _Harness:
    def __init__(self, adapter=None, policy=None, default_timeout=5.0):
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self._tmp.name) / "ws"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.project = self.workspace / "demo"
        self.project.mkdir()
        (self.project / "pyproject.toml").write_text('[project]\nname="demo"', encoding="utf-8")
        self.policy_ws = WorkspacePathPolicy(self.workspace)
        self.resolver = ProjectResolver(self.policy_ws)
        self.exec_policy = policy or CodingExecutionPolicy()
        self.adapter = adapter or FakeOpenCodeAdapter()
        self.bus = EventBus()
        self.manager = CodingTaskManager(
            adapter=self.adapter,
            policy=self.exec_policy,
            bus=self.bus,
            default_timeout=default_timeout,
            max_timeout=60.0,
        )
        registry = ToolRegistry()
        for tool in build_coding_tools(self.manager, self.resolver):
            registry.register(tool)
        self.registry = registry
        self.executor = ToolExecutor(registry, bus=self.bus, policy=DefaultPermissionPolicy())
        # allow-all executor for exercising the tools themselves
        self.executor_all = ToolExecutor(
            registry, bus=EventBus(), policy=_allow_all()
        )

    def call(self, name, args, executor=None):
        return (executor or self.executor_all).execute(ToolCall.new(name, args), _context())

    def cleanup(self):
        self._tmp.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.cleanup()
        return False


def _allow_all():
    from chris.tools import RiskLevel

    return DefaultPermissionPolicy(allowed_risks=list(RiskLevel))


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TestModels(unittest.TestCase):
    def test_coding_task_status_lifecycle(self):
        for status in ("PENDING", "RUNNING", "COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "DENIED"):
            self.assertTrue(hasattr(CodingStatus, status))

    def test_coding_result_to_text(self):
        result = CodingResult(
            status=CodingStatus.COMPLETED,
            summary="fixed bug",
            changed_files=["a.py"],
            tests="PASS",
            exit_code=0,
        )
        text = result.to_text()
        self.assertIn("completed", text)
        self.assertIn("fixed bug", text)
        self.assertIn("a.py", text)
        self.assertIn("PASS", text)

    def test_project_context(self):
        ctx = ProjectContext(project_id="abc", project_root="/x", project_name="x")
        self.assertEqual(ctx.project_name, "x")
        self.assertIn("language", ctx.to_dict())


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


class TestPolicy(unittest.TestCase):
    def test_scope_risk_mapping(self):
        policy = CodingExecutionPolicy()
        self.assertTrue(policy.check_scope("READ_ONLY").allowed)
        self.assertTrue(policy.check_scope(PROJECT_TEST).allowed)
        self.assertFalse(policy.check_scope(GIT_WRITE).allowed)
        self.assertTrue(policy.check_scope(GIT_WRITE).confirmation_required)
        self.assertFalse(policy.check_scope(PROJECT_PACKAGE).allowed)
        self.assertFalse(policy.check_scope(SYSTEM_COMMAND).allowed)

    def test_scope_allowlist_overrides(self):
        policy = CodingExecutionPolicy(allowed_scopes=["GIT_WRITE"])
        decision = policy.check_scope("GIT_WRITE")
        self.assertTrue(decision.allowed)

    def test_unknown_scope_is_critical_denied(self):
        policy = CodingExecutionPolicy()
        decision = policy.check_scope("totally_unknown")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.risk.value, "CRITICAL")

    def test_sanitized_environment_strips_secrets(self):
        with mock.patch.dict(
            "os.environ",
            {
                "PATH": "C:\\bin",
                "ZHIPU_API_KEY": "secret-key-123",
                "DEEPSEEK_API_KEY": "sk-secret",
                "MY_TOKEN": "tok",
                "HOME": "C:\\Users\\x",
                "SAFE_VAR": "ok",
            },
            clear=True,
        ):
            policy = CodingExecutionPolicy()
            env = policy.sanitized_environment()
            self.assertEqual(env.get("PATH"), "C:\\bin")
            self.assertEqual(env.get("HOME"), "C:\\Users\\x")
            self.assertNotIn("ZHIPU_API_KEY", env)
            self.assertNotIn("DEEPSEEK_API_KEY", env)
            self.assertNotIn("MY_TOKEN", env)
            self.assertNotIn("SAFE_VAR", env)  # not in allow-list
            self.assertIn("PATH", DEFAULT_ENV_ALLOWLIST)


# ---------------------------------------------------------------------------
# Project resolution
# ---------------------------------------------------------------------------


class TestProject(unittest.TestCase):
    def test_resolve_project_name(self):
        with _Harness() as h:
            ctx = h.resolver.resolve("demo")
            self.assertIsInstance(ctx, ProjectContext)
            self.assertEqual(ctx.project_name, "demo")
            self.assertEqual(ctx.language, "python")

    def test_resolve_relative_path(self):
        with _Harness() as h:
            ctx = h.resolver.resolve(working_directory="demo")
            self.assertEqual(ctx.project_name, "demo")

    def test_missing_project_raises(self):
        with _Harness() as h:
            from chris.tools.coding.errors import CodingError, CodingErrorCode

            with self.assertRaises(CodingError) as cm:
                h.resolver.resolve("nope")
            self.assertEqual(cm.exception.code, CodingErrorCode.PROJECT_NOT_FOUND)

    def test_no_project_requires_context(self):
        with _Harness() as h:
            from chris.tools.coding.errors import CodingError, CodingErrorCode

            with self.assertRaises(CodingError) as cm:
                h.resolver.resolve()
            self.assertEqual(cm.exception.code, CodingErrorCode.PROJECT_CONTEXT_REQUIRED)

    def test_outside_workspace_rejected(self):
        with _Harness() as h:
            from chris.tools.coding.errors import CodingError, CodingErrorCode

            with self.assertRaises(CodingError) as cm:
                h.resolver.resolve("../secret")
            self.assertEqual(cm.exception.code, CodingErrorCode.PROJECT_NOT_FOUND)

    def test_absolute_outside_rejected(self):
        with _Harness() as h:
            from chris.tools.coding.errors import CodingError

            with self.assertRaises(CodingError):
                h.resolver.resolve("C:\\Windows")


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class TestManager(unittest.TestCase):
    def test_task_completes_and_stores_result(self):
        with _Harness() as h:
            task = h.manager.create_task(project_root=str(h.project), project_name="demo", prompt="add login")
            self.assertEqual(task.status, CodingStatus.PENDING)
            h.manager.start(task)
            deadline = time.time() + 5
            while task.status in (CodingStatus.PENDING, CodingStatus.STARTING, CodingStatus.RUNNING) and time.time() < deadline:
                time.sleep(0.02)
            self.assertEqual(task.status, CodingStatus.COMPLETED)
            self.assertEqual(task.test_status, "PASS")
            self.assertEqual(task.changed_files, ["src/auth.py", "tests/test_auth.py"])

    def test_task_failure(self):
        adapter = FakeOpenCodeAdapter(
            result=CodingResult(status=CodingStatus.FAILED, error="exit code 1", exit_code=1)
        )
        with _Harness(adapter=adapter) as h:
            task = h.manager.create_task(project_root=str(h.project), project_name="demo", prompt="x")
            h.manager.start(task)
            deadline = time.time() + 5
            while task.status is CodingStatus.PENDING and time.time() < deadline:
                time.sleep(0.02)
            self.assertEqual(task.status, CodingStatus.FAILED)

    def test_timeout(self):
        adapter = FakeOpenCodeAdapter(delay=5.0)
        with _Harness(adapter=adapter, default_timeout=0.2) as h:
            task = h.manager.create_task(project_root=str(h.project), project_name="demo", prompt="x", timeout=0.2)
            h.manager.start(task)
            deadline = time.time() + 5
            while task.status in (CodingStatus.PENDING, CodingStatus.STARTING, CodingStatus.RUNNING) and time.time() < deadline:
                time.sleep(0.02)
            self.assertEqual(task.status, CodingStatus.TIMEOUT)

    def test_cancellation(self):
        adapter = FakeOpenCodeAdapter(delay=5.0)
        with _Harness(adapter=adapter) as h:
            task = h.manager.create_task(project_root=str(h.project), project_name="demo", prompt="x")
            h.manager.start(task)
            cancelled = h.manager.cancel(task.task_id)
            self.assertEqual(cancelled.status, CodingStatus.CANCELLED)

    def test_project_busy(self):
        with _Harness() as h:
            h.manager.create_task(project_root=str(h.project), project_name="demo", prompt="a")
            from chris.tools.coding.errors import CodingError, CodingErrorCode

            with self.assertRaises(CodingError) as cm:
                h.manager.create_task(project_root=str(h.project), project_name="demo", prompt="b")
            self.assertEqual(cm.exception.code, CodingErrorCode.PROJECT_BUSY)

    def test_denied_scope(self):
        with _Harness() as h:
            task = h.manager.create_task(project_root=str(h.project), project_name="demo", prompt="x", scope="GIT_WRITE")
            self.assertEqual(task.status, CodingStatus.DENIED)
            self.assertIn("confirmation", task.error)

    def test_events_emitted(self):
        with _Harness() as h:
            seen = []
            h.bus.subscribe(seen.append, EventType.CODING_TASK_STARTED)
            h.bus.subscribe(seen.append, EventType.CODING_TASK_COMPLETED)
            task = h.manager.create_task(project_root=str(h.project), project_name="demo", prompt="x")
            h.manager.start(task)
            deadline = time.time() + 5
            while task.status is CodingStatus.PENDING and time.time() < deadline:
                time.sleep(0.02)
            types = [e.type for e in seen]
            self.assertIn(EventType.CODING_TASK_STARTED, types)
            self.assertIn(EventType.CODING_TASK_COMPLETED, types)

    def test_temporal_records_activity(self):
        with _Harness() as h:
            temporal = mock.Mock()
            manager = CodingTaskManager(
                adapter=h.adapter, policy=h.exec_policy, bus=h.bus, temporal=temporal
            )
            task = manager.create_task(project_root=str(h.project), project_name="demo", prompt="x")
            manager.start(task)
            deadline = time.time() + 5
            while task.status is CodingStatus.PENDING and time.time() < deadline:
                time.sleep(0.02)
            statuses = [c.args[1] for c in temporal.record_tool_activity.call_args_list]
            self.assertIn("STARTED", statuses)
            self.assertIn("COMPLETED", statuses)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


class TestCodingTools(unittest.TestCase):
    def test_submit_returns_task_id(self):
        with _Harness() as h:
            result = h.call("coding_task", {"project": "demo", "prompt": "add login"})
            self.assertEqual(result.status, ToolStatus.SUCCESS)
            self.assertIn("Task id:", result.content)
            self.assertIn("task_id", result.metadata)

    def test_submit_opencode_not_installed(self):
        adapter = FakeOpenCodeAdapter()
        adapter.available = False
        with _Harness(adapter=adapter) as h:
            from chris.tools.coding.tool import CodingTaskTool

            tool = next(t for t in build_coding_tools(h.manager, h.resolver) if t.name == "coding_task")
            # direct tool call so the fake adapter's availability is used
            result = tool.execute(_context(), {"project": "demo", "prompt": "x"})
            self.assertEqual(result.status, ToolStatus.FAILURE)
            self.assertEqual(result.metadata["error_code"], "opencode_not_installed")

    def test_status_polls_completed(self):
        with _Harness() as h:
            submitted = h.call("coding_task", {"project": "demo", "prompt": "add login"})
            task_id = submitted.metadata["task_id"]
            deadline = time.time() + 5
            status = None
            while time.time() < deadline:
                poll = h.call("coding_task_status", {"task_id": task_id})
                status = poll.metadata.get("status")
                if status in ("COMPLETED", "FAILED", "TIMEOUT"):
                    break
                time.sleep(0.02)
            self.assertEqual(status, "COMPLETED")
            self.assertIn("implemented login", poll.content)

    def test_cancel_tool(self):
        adapter = FakeOpenCodeAdapter(delay=5.0)
        with _Harness(adapter=adapter) as h:
            submitted = h.call("coding_task", {"project": "demo", "prompt": "x"})
            task_id = submitted.metadata["task_id"]
            cancelled = h.call("coding_task_cancel", {"task_id": task_id})
            self.assertEqual(cancelled.status, ToolStatus.SUCCESS)
            self.assertIn("CANCELLED", cancelled.content)

    def test_validation_missing_prompt(self):
        with _Harness() as h:
            result = h.call("coding_task", {"project": "demo"})
            self.assertEqual(result.status, ToolStatus.FAILURE)
            self.assertTrue(any("missing required" in e for e in result.metadata.get("validation", [])))

    def test_permission_medium_denied_by_default(self):
        with _Harness() as h:
            result = h.call("coding_task", {"project": "demo", "prompt": "x"}, executor=h.executor)
            self.assertEqual(result.status, ToolStatus.DENIED)
            self.assertEqual(result.metadata["error_code"], "confirmation_required")


class TestPermissionModel(unittest.TestCase):
    def test_coding_tool_risks(self):
        from chris.tools import RiskLevel
        from chris.tools.coding.tool import CodingTaskCancelTool, CodingTaskStatusTool, CodingTaskTool

        with _Harness() as h:
            status_tool = CodingTaskStatusTool(h.manager)
            self.assertEqual(status_tool.risk_level, RiskLevel.READ_ONLY)
            default = DefaultPermissionPolicy()
            self.assertTrue(default.check(status_tool.definition(), _context()).allowed)
            # coding_task is MEDIUM -> denied by default policy
            self.assertEqual(CodingTaskTool(h.manager, h.resolver).risk_level, RiskLevel.MEDIUM)
            self.assertFalse(default.check(CodingTaskTool(h.manager, h.resolver).definition(), _context()).allowed)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class TestParser(unittest.TestCase):
    def test_test_status_detection(self):
        from chris.tools.coding.parser import CodingResultParser

        parser = CodingResultParser(git=False)
        result = CodingResult(status=CodingStatus.COMPLETED)
        parser.build("", result, raw_output="Ran 5 tests... 5 passed")
        self.assertEqual(result.tests, "PASS")
        result2 = CodingResult(status=CodingStatus.FAILED)
        parser.build("", result2, raw_output="test_foo FAILED")
        self.assertEqual(result2.tests, "FAILED")
        result3 = CodingResult(status=CodingStatus.COMPLETED)
        parser.build("", result3, raw_output="all done, no tests")
        self.assertEqual(result3.tests, "NOT_RUN")


# ---------------------------------------------------------------------------
# Full Tool Core + LLM loop
# ---------------------------------------------------------------------------


class _CodingLLM:
    """Fake LLM: round 1 submits coding_task, round 2 answers from the result."""

    def __init__(self):
        self.rounds = 0
        self.last_messages = None
        self.final = "登录功能已完成，测试通过。改动文件：src/auth.py"

    def generate_with_tools(self, messages, tools=None, **kwargs):
        self.rounds += 1
        self.last_messages = list(messages)
        if self.rounds == 1:
            return __import__("chris.llm.base", fromlist=["LLMResponse"]).LLMResponse(
                content="",
                tool_calls=[
                    {
                        "id": "c1",
                        "name": "coding_task",
                        "arguments": {"project": "demo", "prompt": "add login"},
                    }
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


class TestEndToEndLoop(unittest.TestCase):
    def test_llm_toolcall_coding_result_llm(self):
        from chris.tools.pipeline import run_tool_pipeline

        with _Harness() as h:
            runtime = _StubRuntime(h)
            runtime.provider = _CodingLLM()
            reply = run_tool_pipeline(
                runtime, [{"role": "user", "content": "帮我把登录功能加上"}]
            )
            self.assertEqual(reply, runtime.provider.final)
            self.assertEqual(runtime.provider.rounds, 2)


if __name__ == "__main__":
    unittest.main()
