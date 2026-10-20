"""Computer Action tools (Phase 8.5).

Each action goes through the Tool Core: ToolRegistry -> ToolExecutor ->
Permission -> ComputerActionTool -> ComputerActionManager -> ComputerBackend.
These tools submit one action (or a bounded sequence) and return the structured
ComputerActionResult. No tool layer touches the OS directly.

Phase 8.5 is fixed-coordinate actions only - no semantic targeting ("click the
send button") - that belongs to Phase 9 Vision.
"""

from __future__ import annotations

from typing import Any

from chris.tools.base import Tool, ToolContext
from chris.tools.computer.errors import ComputerError, ComputerErrorCode
from chris.tools.computer.manager import ComputerActionManager
from chris.tools.computer.models import ComputerActionResult
from chris.tools.models import RiskLevel, ToolResult, ToolStatus


def _result_for(tool_name: str, result: ComputerActionResult) -> ToolResult:
    metadata = {
        "status": result.status.value,
        "action_id": result.action_id,
        "error_code": result.error_code,
        "duration_seconds": result.duration_seconds,
    }
    return ToolResult(
        tool_name=tool_name,
        status=ToolStatus.DENIED
        if result.status.value == "denied"
        else ToolStatus.FAILURE
        if result.status.value in ("failed", "timeout", "cancelled")
        else ToolStatus.SUCCESS,
        content=result.to_text(),
        error=result.error,
        metadata=metadata,
    )


def _failure(tool_name: str, exc: ComputerError) -> ToolResult:
    return ToolResult(
        tool_name=tool_name,
        status=ToolStatus.FAILURE,
        error=exc.message,
        metadata={"error_code": exc.code.value},
    )


class _ComputerActionBase(Tool):
    action_type = ""

    def __init__(self, manager: ComputerActionManager) -> None:
        self.manager = manager

    @property
    def permission(self):
        return self.manager.policy.risk_for(self.action_type)

    @property
    def risk_level(self):
        return self.manager.policy.risk_for(self.action_type)

    def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        return self._run(arguments, authorized=bool(getattr(context, "approved", False)))

    def _run(self, arguments: dict[str, Any], authorized: bool = False) -> ToolResult:
        try:
            result = self.manager.run_action(
                self.action_type, arguments, authorized=authorized
            )
        except ComputerError as exc:
            return _failure(self.name, exc)
        return _result_for(self.name, result)


class ComputerMoveMouseTool(_ComputerActionBase):
    name = "computer_move_mouse"
    description = "Move the mouse cursor to absolute screen coordinates."
    action_type = "move_mouse"
    input_schema = {
        "type": "object",
        "properties": {
            "x": {"type": "integer"}, "y": {"type": "integer"},
        },
        "required": ["x", "y"], "additionalProperties": False,
    }


class ComputerClickTool(_ComputerActionBase):
    name = "computer_click"
    description = "Click at absolute screen coordinates (left/right)."
    action_type = "click"
    input_schema = {
        "type": "object",
        "properties": {
            "x": {"type": "integer"}, "y": {"type": "integer"},
            "button": {"type": "string", "enum": ["left", "right"], "default": "left"},
        },
        "required": ["x", "y"], "additionalProperties": False,
    }


class ComputerDoubleClickTool(_ComputerActionBase):
    name = "computer_double_click"
    description = "Double-click at absolute screen coordinates."
    action_type = "double_click"
    input_schema = {
        "type": "object",
        "properties": {
            "x": {"type": "integer"}, "y": {"type": "integer"},
            "button": {"type": "string", "enum": ["left", "right"], "default": "left"},
        },
        "required": ["x", "y"], "additionalProperties": False,
    }


class ComputerScrollTool(_ComputerActionBase):
    name = "computer_scroll"
    description = "Scroll at absolute screen coordinates by a wheel amount."
    action_type = "scroll"
    input_schema = {
        "type": "object",
        "properties": {
            "x": {"type": "integer"}, "y": {"type": "integer"},
            "amount": {"type": "integer"},
        },
        "required": ["x", "y", "amount"], "additionalProperties": False,
    }


class ComputerKeyPressTool(_ComputerActionBase):
    name = "computer_key_press"
    description = "Press a single key (e.g. enter, space, tab, a, 1, f5)."
    action_type = "key_press"
    input_schema = {
        "type": "object",
        "properties": {"key": {"type": "string"}},
        "required": ["key"], "additionalProperties": False,
    }


class ComputerHotkeyTool(_ComputerActionBase):
    name = "computer_hotkey"
    description = "Press a combination of keys (e.g. ['ctrl', 's'])."
    action_type = "hotkey"
    input_schema = {
        "type": "object",
        "properties": {"keys": {"type": "array", "items": {"type": "string"}}},
        "required": ["keys"], "additionalProperties": False,
    }


class ComputerTypeTextTool(_ComputerActionBase):
    name = "computer_type_text"
    description = "Type text at the current cursor position."
    action_type = "type_text"
    input_schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"], "additionalProperties": False,
    }


class ComputerScreenshotTool(_ComputerActionBase):
    name = "computer_screenshot"
    description = (
        "Capture the screen and return its size/format metadata. Acquisition "
        "only - screen understanding belongs to a later vision phase."
    )
    action_type = "screenshot"
    input_schema = {"type": "object", "properties": {}, "required": []}


class ComputerScreenInfoTool(_ComputerActionBase):
    name = "computer_screen_info"
    description = "Get the current screen size in pixels."
    action_type = "get_screen_size"
    input_schema = {"type": "object", "properties": {}, "required": []}


class ComputerActiveWindowTool(_ComputerActionBase):
    name = "computer_active_window"
    description = "Get basic info about the active window (title/process/bounds)."
    action_type = "get_active_window"
    input_schema = {"type": "object", "properties": {}, "required": []}


class ComputerWaitTool(_ComputerActionBase):
    name = "computer_wait"
    description = "Wait (pause) for a number of seconds."
    action_type = "wait"
    input_schema = {
        "type": "object",
        "properties": {"seconds": {"type": "number", "minimum": 0, "maximum": 60}},
        "required": ["seconds"], "additionalProperties": False,
    }


class ComputerSequenceTool(Tool):
    name = "computer_sequence"
    description = (
        "Run a bounded sequence of computer actions. Steps: [{\"type\": ..., "
        "\"arguments\": {...}}]. Fails fast on the first failing step."
    )
    permission = RiskLevel.HIGH
    risk_level = RiskLevel.HIGH
    input_schema = {
        "type": "object",
        "properties": {
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string"},
                        "arguments": {"type": "object"},
                    },
                    "required": ["type"],
                },
            },
            "timeout": {"type": "integer"},
        },
        "required": ["steps"], "additionalProperties": False,
    }

    def __init__(self, manager: ComputerActionManager) -> None:
        self.manager = manager

    def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        try:
            result = self.manager.run_sequence(
                arguments["steps"],
                timeout=arguments.get("timeout"),
                cancel_event=context.cancel_event,
            )
        except ComputerError as exc:
            return _failure(self.name, exc)
        return _result_for(self.name, result)


class ComputerActionStatusTool(Tool):
    name = "computer_action_status"
    description = "Check the status of a previously run computer action."
    permission = RiskLevel.READ_ONLY
    risk_level = RiskLevel.READ_ONLY
    input_schema = {
        "type": "object",
        "properties": {"action_id": {"type": "string"}},
        "required": ["action_id"], "additionalProperties": False,
    }

    def __init__(self, manager: ComputerActionManager) -> None:
        self.manager = manager

    def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        action = self.manager.get(arguments.get("action_id", ""))
        if action is None:
            return ToolResult(
                tool_name=self.name,
                status=ToolStatus.FAILURE,
                error="action not found",
                metadata={"error_code": "action_not_found"},
            )
        return _result_for(
            self.name,
            ComputerActionResult(
                status=action.status,
                action_id=action.action_id,
                action_type=action.action_type,
                error=action.error,
                metadata=action.metadata,
            ),
        )


class ComputerActionCancelTool(Tool):
    name = "computer_action_cancel"
    description = "Request cancellation of a running computer action."
    permission = RiskLevel.MEDIUM
    risk_level = RiskLevel.MEDIUM
    input_schema = {
        "type": "object",
        "properties": {"action_id": {"type": "string"}},
        "required": ["action_id"], "additionalProperties": False,
    }

    def __init__(self, manager: ComputerActionManager) -> None:
        self.manager = manager

    def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        action = self.manager.get(arguments.get("action_id", ""))
        if action is None:
            return ToolResult(
                tool_name=self.name,
                status=ToolStatus.FAILURE,
                error="action not found",
                metadata={"error_code": "action_not_found"},
            )
        return ToolResult(
            tool_name=self.name,
            status=ToolStatus.SUCCESS,
            content=f"Action {action.action_id} status: {action.status.value}.",
            metadata={"action_id": action.action_id, "status": action.status.value},
        )


def build_computer_tools(manager: ComputerActionManager) -> list[Tool]:
    return [
        ComputerMoveMouseTool(manager),
        ComputerClickTool(manager),
        ComputerDoubleClickTool(manager),
        ComputerScrollTool(manager),
        ComputerKeyPressTool(manager),
        ComputerHotkeyTool(manager),
        ComputerTypeTextTool(manager),
        ComputerScreenshotTool(manager),
        ComputerScreenInfoTool(manager),
        ComputerActiveWindowTool(manager),
        ComputerWaitTool(manager),
        ComputerSequenceTool(manager),
        ComputerActionStatusTool(manager),
        ComputerActionCancelTool(manager),
    ]
