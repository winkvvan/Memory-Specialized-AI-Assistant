"""Coding task tools (Phase 8.4).

Expose the CodingTaskManager through the Tool Core:

  coding_task          - submit a coding task (non-blocking: returns the task_id
                         and status immediately; the OpenCode process runs in a
                         background thread so it never blocks the GUI/executor).
  coding_task_status   - poll a task and get its current status / result.
  coding_task_cancel   - request cancellation of a running task.

The tools never touch subprocess directly - that is the adapter's job. Tool
results are structured CodingResult projections, not raw OpenCode output.
"""

from __future__ import annotations

from typing import Any

from chris.tools.base import Tool, ToolContext
from chris.tools.coding.errors import CodingError
from chris.tools.coding.manager import CodingTaskManager
from chris.tools.coding.models import CodingResult, CodingStatus, ProjectContext
from chris.tools.coding.policy import GIT_READ, PROJECT_TEST
from chris.tools.coding.project import ProjectResolver
from chris.tools.models import RiskLevel, ToolResult, ToolStatus

_SUBMIT_SCHEMA = {
    "type": "object",
    "properties": {
        "project": {"type": "string", "description": "Project name or workspace-relative path."},
        "prompt": {"type": "string", "description": "The development task for the coding agent."},
        "working_directory": {"type": "string", "description": "Optional workspace-relative directory."},
        "scope": {
            "type": "string",
            "enum": ["READ_ONLY", "PROJECT_BUILD", "PROJECT_TEST", "PROJECT_LINT", "GIT_READ"],
            "default": "PROJECT_TEST",
            "description": "What the coding agent is allowed to do.",
        },
        "timeout": {"type": "integer", "description": "Optional timeout in seconds."},
    },
    "required": ["project", "prompt"],
    "additionalProperties": False,
}


class CodingTaskTool(Tool):
    name = "coding_task"
    description = (
        "Submit a development task (implement / fix / refactor / test) to the "
        "external coding agent (OpenCode). Returns a task_id immediately; poll "
        "with coding_task_status. Project must be a workspace project. Requires "
        "the coding agent to be installed."
    )
    permission = RiskLevel.MEDIUM
    risk_level = RiskLevel.MEDIUM
    input_schema = _SUBMIT_SCHEMA

    def __init__(
        self,
        manager: CodingTaskManager,
        resolver: ProjectResolver,
        default_scope: str = PROJECT_TEST,
    ) -> None:
        self.manager = manager
        self.resolver = resolver
        self.default_scope = default_scope

    def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        project = arguments.get("project")
        prompt = arguments.get("prompt", "")
        working_directory = arguments.get("working_directory")
        scope = arguments.get("scope", self.default_scope)
        authorized = bool(getattr(context, "approved", False))

        try:
            if not self.manager.adapter.is_available():
                return ToolResult(
                    tool_name=self.name,
                    status=ToolStatus.FAILURE,
                    error="opencode is not installed; coding tasks are unavailable",
                    metadata={"error_code": "opencode_not_installed"},
                )
            project_ctx: ProjectContext = self.resolver.resolve(project, working_directory)
            task = self.manager.create_task(
                project_root=project_ctx.project_root,
                project_name=project_ctx.project_name,
                prompt=prompt,
                scope=scope,
                timeout=arguments.get("timeout"),
                authorized=authorized,
            )
        except CodingError as exc:
            return self._failure(exc)

        if task.status is CodingStatus.DENIED:
            return ToolResult(
                tool_name=self.name,
                status=ToolStatus.DENIED,
                error=task.error or "task scope requires confirmation",
                metadata={"error_code": "confirmation_required", "task_id": task.task_id},
            )

        try:
            self.manager.start(task)
        except CodingError as exc:
            return self._failure(exc)

        return ToolResult(
            tool_name=self.name,
            status=ToolStatus.SUCCESS,
            content=(
                f"Coding task submitted.\n"
                f"Task id: {task.task_id}\n"
                f"Project: {project_ctx.project_name}\n"
                f"Status: {task.status.value}\n"
                f"Prompt: {task.prompt[:200]}"
            ),
            metadata={
                "task_id": task.task_id,
                "project": project_ctx.project_name,
                "project_root": project_ctx.project_root,
                "status": task.status.value,
                "language": project_ctx.language,
                "framework": project_ctx.framework,
                "scope": scope,
            },
        )

    def _failure(self, exc: CodingError) -> ToolResult:
        return ToolResult(
            tool_name=self.name,
            status=ToolStatus.FAILURE,
            error=exc.message,
            metadata={"error_code": exc.code.value},
        )


class CodingTaskStatusTool(Tool):
    name = "coding_task_status"
    description = (
        "Check the status of a previously submitted coding task and get its "
        "structured result (summary, changed files, test status, exit code)."
    )
    permission = RiskLevel.READ_ONLY
    risk_level = RiskLevel.READ_ONLY
    input_schema = {
        "type": "object",
        "properties": {"task_id": {"type": "string"}},
        "required": ["task_id"],
        "additionalProperties": False,
    }

    def __init__(self, manager: CodingTaskManager) -> None:
        self.manager = manager

    def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        task_id = arguments.get("task_id", "")
        task = self.manager.get(task_id)
        if task is None:
            return ToolResult(
                tool_name=self.name,
                status=ToolStatus.FAILURE,
                error=f"no coding task {task_id!r}",
                metadata={"error_code": "task_not_found"},
            )
        result = CodingResult(
            status=task.status,
            summary=task.summary,
            changed_files=list(task.changed_files),
            tests=task.test_status,
            exit_code=task.exit_code,
            error=task.error,
        )
        return ToolResult(
            tool_name=self.name,
            status=ToolStatus.SUCCESS,
            content=result.to_text(),
            metadata={
                "task_id": task.task_id,
                "status": task.status.value,
                "exit_code": task.exit_code,
                "test_status": task.test_status,
                "changed_files": list(task.changed_files),
            },
        )


class CodingTaskCancelTool(Tool):
    name = "coding_task_cancel"
    description = "Request cancellation of a running coding task."
    permission = RiskLevel.MEDIUM
    risk_level = RiskLevel.MEDIUM
    input_schema = {
        "type": "object",
        "properties": {"task_id": {"type": "string"}},
        "required": ["task_id"],
        "additionalProperties": False,
    }

    def __init__(self, manager: CodingTaskManager) -> None:
        self.manager = manager

    def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        task_id = arguments.get("task_id", "")
        try:
            task = self.manager.cancel(task_id)
        except CodingError as exc:
            return ToolResult(
                tool_name=self.name,
                status=ToolStatus.FAILURE,
                error=exc.message,
                metadata={"error_code": exc.code.value},
            )
        return ToolResult(
            tool_name=self.name,
            status=ToolStatus.SUCCESS,
            content=f"Coding task {task.task_id} status: {task.status.value}.",
            metadata={"task_id": task.task_id, "status": task.status.value},
        )


def build_coding_tools(manager: CodingTaskManager, resolver: ProjectResolver) -> list[Tool]:
    return [
        CodingTaskTool(manager, resolver),
        CodingTaskStatusTool(manager),
        CodingTaskCancelTool(manager),
    ]
