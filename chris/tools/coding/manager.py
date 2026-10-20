"""CodingTaskManager: schedules, runs and tracks coding tasks.

Lifecycle: PENDING -> STARTING -> RUNNING -> COMPLETED / FAILED / CANCELLED /
TIMEOUT / DENIED. Runs the OpenCode process in a background thread (never
blocking the GUI thread), emits CODING_* events on the existing EventBus, and
records tool activity on Temporal. Enforces one running task per project
(project_busy) and a configurable timeout.
"""

from __future__ import annotations

import threading
from typing import Any, Callable

from chris.events import Event, EventBus, EventType
from chris.tools.coding.adapter import OpenCodeAdapter
from chris.tools.coding.errors import CodingError, CodingErrorCode
from chris.tools.coding.models import CodingResult, CodingStatus, CodingTask
from chris.tools.coding.policy import CodingExecutionPolicy, ScopeDecision
from chris.tools.models import RiskLevel

MAX_OUTPUT_LINES = 200


class CodingTaskManager:
    def __init__(
        self,
        adapter: OpenCodeAdapter | None = None,
        policy: CodingExecutionPolicy | None = None,
        bus: EventBus | None = None,
        temporal=None,
        default_timeout: float = 120.0,
        max_timeout: float = 600.0,
        scope_resolver: Callable[[str], ScopeDecision] | None = None,
    ) -> None:
        self.adapter = adapter or OpenCodeAdapter()
        self.policy = policy or CodingExecutionPolicy()
        self.bus = bus
        self.temporal = temporal
        self.default_timeout = default_timeout
        self.max_timeout = max_timeout
        self._scope_resolver = scope_resolver or self.policy.check_scope
        self._tasks: dict[str, CodingTask] = {}
        self._by_project: dict[str, CodingTask] = {}
        self._lock = threading.Lock()

    # -- task management ------------------------------------------------

    def create_task(
        self,
        *,
        project_root: str,
        project_name: str,
        prompt: str,
        scope: str = "PROJECT_TEST",
        timeout: float | None = None,
        provider: str = "opencode",
        extra_args: list[str] | None = None,
        allow_reuse: bool = False,
        authorized: bool = False,
    ) -> CodingTask:
        if not prompt or not prompt.strip():
            raise CodingError(CodingErrorCode.INVALID_ARGUMENT, "prompt must be non-empty")
        decision = self._scope_resolver(scope)
        task = CodingTask(
            project_root=project_root,
            project_name=project_name,
            prompt=prompt,
            status=CodingStatus.PENDING,
            provider=provider,
            metadata={
                "scope": scope,
                "risk": decision.risk.value,
                "timeout": min(timeout or self.default_timeout, self.max_timeout),
            },
        )
        # CRITICAL (SYSTEM_COMMAND / unknown scope) is never confirmable - deny
        # even when authorized upstream. Confirmable scopes proceed when the
        # executor already recorded user approval.
        if not decision.allowed:
            if decision.risk is not RiskLevel.CRITICAL and authorized:
                pass
            else:
                task.status = CodingStatus.DENIED
                task.error = decision.reason or "scope requires confirmation"
                task.completed_at = task.created_at
                self._emit(EventType.CODING_TASK_DENIED, task)
                with self._lock:
                    self._tasks[task.task_id] = task
                return task

        if not allow_reuse:
            with self._lock:
                if project_root in self._by_project:
                    existing = self._by_project[project_root]
                    if existing.status in (
                        CodingStatus.PENDING,
                        CodingStatus.STARTING,
                        CodingStatus.RUNNING,
                        CodingStatus.WAITING,
                    ):
                        raise CodingError(
                            CodingErrorCode.PROJECT_BUSY,
                            f"project {project_name!r} already has a running coding task",
                            detail={"task_id": existing.task_id},
                        )
        with self._lock:
            self._tasks[task.task_id] = task
            self._by_project[project_root] = task
        return task

    def start(self, task: CodingTask, *, extra_args: list[str] | None = None) -> None:
        if task.status is not CodingStatus.PENDING:
            raise CodingError(
                CodingErrorCode.EXECUTION_ERROR, "task is not in PENDING state"
            )
        task.status = CodingStatus.STARTING
        task.started_at = task.created_at
        self._emit(EventType.CODING_TASK_STARTED, task)
        self._record_activity(task, "STARTED")

        def _run() -> None:
            try:
                task.status = CodingStatus.RUNNING
                self.adapter.start(task.project_root, task.prompt, extra_args=extra_args)
                timeout = float(task.metadata.get("timeout", self.default_timeout))
                result = self.adapter.wait(timeout=timeout)
            except CodingError as exc:
                self._finish(task, CodingStatus.FAILED, error=exc.message, exit_code=None)
                return
            except Exception as exc:  # noqa: BLE001
                self._finish(
                    task,
                    CodingStatus.FAILED,
                    error=f"{type(exc).__name__}: {exc}",
                    exit_code=None,
                )
                return
            self._apply_result(task, result)

        worker = threading.Thread(target=_run, daemon=True, name=f"coding-{task.task_id[:8]}")
        worker.start()

    def get(self, task_id: str) -> CodingTask | None:
        with self._lock:
            return self._tasks.get(task_id)

    def cancel(self, task_id: str) -> CodingTask:
        with self._lock:
            task = self._tasks.get(task_id)
        if task is None:
            raise CodingError(CodingErrorCode.NOT_FOUND, f"no coding task {task_id!r}")
        if task.status in (CodingStatus.COMPLETED, CodingStatus.FAILED, CodingStatus.CANCELLED, CodingStatus.TIMEOUT):
            return task
        self.adapter.cancel()
        task.status = CodingStatus.CANCELLED
        self._emit(EventType.CODING_TASK_CANCELLED, task)
        self._record_activity(task, "CANCELLED")
        return task

    def project_running(self, project_root: str) -> str | None:
        with self._lock:
            task = self._by_project.get(project_root)
            if task and task.status in (
                CodingStatus.PENDING,
                CodingStatus.STARTING,
                CodingStatus.RUNNING,
                CodingStatus.WAITING,
            ):
                return task.task_id
        return None

    # -- internals ------------------------------------------------------

    def _apply_result(self, task: CodingTask, result: CodingResult) -> None:
        task.exit_code = result.exit_code
        task.summary = result.summary
        task.changed_files = list(result.changed_files)
        task.test_status = result.tests
        task.error = result.error
        if result.status is CodingStatus.COMPLETED:
            self._finish(task, CodingStatus.COMPLETED)
        elif result.status is CodingStatus.TIMEOUT:
            self._finish(task, CodingStatus.TIMEOUT, error=result.error)
        elif result.status is CodingStatus.CANCELLED:
            self._finish(task, CodingStatus.CANCELLED, error=result.error)
        else:
            self._finish(task, CodingStatus.FAILED, error=result.error)

    def _finish(self, task: CodingTask, status: CodingStatus, error: str | None = None) -> None:
        task.status = status
        task.completed_at = task.created_at
        if error:
            task.error = error
        event_type = {
            CodingStatus.COMPLETED: EventType.CODING_TASK_COMPLETED,
            CodingStatus.FAILED: EventType.CODING_TASK_FAILED,
            CodingStatus.CANCELLED: EventType.CODING_TASK_CANCELLED,
            CodingStatus.TIMEOUT: EventType.CODING_TASK_FAILED,
        }.get(status, EventType.CODING_TASK_FAILED)
        self._emit(event_type, task)
        self._record_activity(task, status.value)

    def _emit(self, event_type: EventType, task: CodingTask) -> None:
        if self.bus is None:
            return
        payload = {
            "task_id": task.task_id,
            "project": task.project_name,
            "project_root": task.project_root,
            "status": task.status.value,
            "error": task.error,
            "changed_files": list(task.changed_files),
            "test_status": task.test_status,
        }
        self.bus.publish(Event(event_type, "coding", payload))

    def _record_activity(self, task: CodingTask, status: str) -> None:
        if self.temporal is None:
            return
        try:
            self.temporal.record_tool_activity(
                "coding_task",
                status,
                duration_seconds=None,
                call_id=task.task_id,
            )
        except Exception:  # noqa: BLE001
            pass
