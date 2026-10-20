"""ComputerActionManager: action lifecycle, cancellation, timeout, sequencing,
concurrency, EventBus and Temporal integration.

Lifecycle: PENDING -> STARTING -> RUNNING -> COMPLETED / FAILED / CANCELLED /
TIMEOUT / DENIED. Runs actions synchronously (they are fast); sequences are
bounded by max_actions and max_duration and check cancel_event between steps.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

from chris.events import Event, EventBus, EventType
from chris.tools.computer.backend import ComputerBackend
from chris.tools.computer.errors import ComputerError, ComputerErrorCode
from chris.tools.computer.models import (
    ComputerAction,
    ComputerActionResult,
    ComputerStatus,
)
from chris.tools.computer.policy import ComputerActionPolicy
from chris.tools.models import RiskLevel

_STATUS_TO_EVENT = {
    ComputerStatus.COMPLETED: EventType.COMPUTER_ACTION_COMPLETED,
    ComputerStatus.FAILED: EventType.COMPUTER_ACTION_FAILED,
    ComputerStatus.CANCELLED: EventType.COMPUTER_ACTION_CANCELLED,
    ComputerStatus.TIMEOUT: EventType.COMPUTER_ACTION_FAILED,
}


class ComputerActionManager:
    def __init__(
        self,
        backend: ComputerBackend,
        policy: ComputerActionPolicy | None = None,
        bus: EventBus | None = None,
        temporal=None,
        default_timeout: float = 10.0,
        max_timeout: float = 60.0,
        max_actions_per_sequence: int = 20,
        max_type_text_length: int = 2000,
        screen_provider: Callable[[], tuple[int, int]] | None = None,
    ) -> None:
        self.backend = backend
        self.policy = policy or ComputerActionPolicy()
        self.bus = bus
        self.temporal = temporal
        self.default_timeout = default_timeout
        self.max_timeout = max_timeout
        self.max_actions_per_sequence = max_actions_per_sequence
        self.max_type_text_length = max_type_text_length
        self._screen_provider = screen_provider or self._default_screen
        self._actions: dict[str, ComputerAction] = {}
        self._lock = threading.Lock()

    def _default_screen(self) -> tuple[int, int]:
        try:
            size = self.backend.get_screen_size()
            return int(size["width"]), int(size["height"])
        except Exception:  # noqa: BLE001
            return 1920, 1080

    # -- action dispatch -------------------------------------------------

    def run_action(
        self,
        action_type: str,
        arguments: dict[str, Any],
        *,
        source: str = "llm",
        timeout: float | None = None,
        authorized: bool = False,
    ) -> ComputerActionResult:
        action = ComputerAction(
            action_type=action_type,
            arguments=dict(arguments),
            source=source,
        )
        with self._lock:
            self._actions[action.action_id] = action

        # permission
        allowed, risk, confirm = self.policy.decision(action_type)
        action.risk_level = risk.value
        # CRITICAL is never confirmable - deny even when authorized upstream.
        if risk is RiskLevel.CRITICAL:
            action.status = ComputerStatus.DENIED
            action.error = f"action {action_type!r} is not allowed (CRITICAL)"
            action.completed_at = action.created_at
            self._emit(EventType.COMPUTER_ACTION_DENIED, action)
            self._record_temporal(action, "DENIED")
            return ComputerActionResult(
                status=ComputerStatus.DENIED,
                action_id=action.action_id,
                action_type=action_type,
                error=action.error,
                error_code=ComputerErrorCode.PERMISSION_DENIED.value,
            )
        if not allowed and not authorized:
            action.status = ComputerStatus.DENIED
            action.error = (
                f"action {action_type!r} requires confirmation (Phase 8.6 not implemented)"
                if confirm
                else f"action {action_type!r} not allowed"
            )
            action.completed_at = action.created_at
            self._emit(EventType.COMPUTER_ACTION_DENIED, action)
            self._record_temporal(action, "DENIED")
            return ComputerActionResult(
                status=ComputerStatus.DENIED,
                action_id=action.action_id,
                action_type=action_type,
                error=action.error,
                error_code=ComputerErrorCode.CONFIRMATION_REQUIRED.value
                if confirm
                else ComputerErrorCode.PERMISSION_DENIED.value,
            )

        requested = timeout if timeout is not None else self.default_timeout
        if requested > self.max_timeout:
            action.status = ComputerStatus.FAILED
            action.error = f"timeout {requested}s exceeds max {self.max_timeout}s"
            action.completed_at = action.created_at
            return ComputerActionResult(
                status=ComputerStatus.FAILED,
                action_id=action.action_id,
                action_type=action_type,
                error=action.error,
                error_code=ComputerErrorCode.TIMEOUT_LIMIT_EXCEEDED.value,
            )
        action.metadata["timeout"] = requested
        action.metadata["screen"] = list(self._screen_provider())

        try:
            result = self._execute_with_timeout(action, requested)
        except ComputerError as exc:
            action.status = ComputerStatus.FAILED
            action.error = exc.message
            action.metadata["error_code"] = exc.code.value
            action.completed_at = action.created_at
            self._finish(action)
            return ComputerActionResult(
                status=ComputerStatus.FAILED,
                action_id=action.action_id,
                action_type=action_type,
                error=exc.message,
                error_code=exc.code.value,
            )
        return result

    # -- sequence --------------------------------------------------------

    def run_sequence(
        self,
        steps: list[dict[str, Any]],
        *,
        source: str = "llm",
        timeout: float | None = None,
        cancel_event: threading.Event | None = None,
    ) -> ComputerActionResult:
        """Run a bounded action sequence. Any step failure stops the rest."""
        if not isinstance(steps, list) or not steps:
            raise ComputerError(
                ComputerErrorCode.INVALID_ACTION, "sequence must be a non-empty list"
            )
        if len(steps) > self.max_actions_per_sequence:
            raise ComputerError(
                ComputerErrorCode.SEQUENCE_TOO_LONG,
                f"sequence has more than {self.max_actions_per_sequence} actions",
            )

        action = ComputerAction(
            action_type="sequence",
            arguments={"steps": steps},
            source=source,
        )
        with self._lock:
            self._actions[action.action_id] = action
        action.risk_level = "HIGH"
        action.status = ComputerStatus.RUNNING
        action.started_at = action.created_at
        self._emit(EventType.COMPUTER_ACTION_STARTED, action)

        started = time.monotonic()
        limit = timeout if timeout is not None else self.default_timeout
        for index, step in enumerate(steps):
            if cancel_event is not None and cancel_event.is_set():
                action.status = ComputerStatus.CANCELLED
                action.error = "sequence cancelled"
                break
            if time.monotonic() - started >= limit:
                action.status = ComputerStatus.TIMEOUT
                action.error = f"sequence timed out after {limit}s"
                break
            step_type = step.get("type", "")
            step_args = step.get("arguments", {})
            try:
                self._dispatch_sync(step_type, step_args)
                action.metadata.setdefault("completed_steps", []).append(index)
            except ComputerError as exc:
                action.status = ComputerStatus.FAILED
                action.error = f"step {index} ({step_type}) failed: {exc.message}"
                action.metadata["error_code"] = exc.code.value
                break
            except Exception as exc:  # noqa: BLE001
                action.status = ComputerStatus.FAILED
                action.error = f"step {index} ({step_type}) failed: {type(exc).__name__}"
                break

        if action.status is ComputerStatus.RUNNING:
            action.status = ComputerStatus.COMPLETED
        action.completed_at = action.created_at
        action.metadata["duration_seconds"] = time.monotonic() - started
        self._finish(action)
        return self._result_from(action)

    # -- cancel / query --------------------------------------------------

    def get(self, action_id: str) -> ComputerAction | None:
        with self._lock:
            return self._actions.get(action_id)

    # -- internals ------------------------------------------------------

    def _execute_with_timeout(
        self, action: ComputerAction, timeout: float
    ) -> ComputerActionResult:
        action.status = ComputerStatus.STARTING
        action.started_at = action.created_at
        self._emit(EventType.COMPUTER_ACTION_STARTED, action)
        self._record_temporal(action, "STARTED")

        box: dict[str, Any] = {}

        def _target() -> None:
            try:
                box["result"] = self._dispatch_sync(action.action_type, action.arguments)
            except ComputerError as exc:
                box["error"] = exc
            except Exception as exc:  # noqa: BLE001
                box["error"] = ComputerError(
                    ComputerErrorCode.BACKEND_ERROR,
                    f"{type(exc).__name__}: {exc}",
                )

        worker = threading.Thread(target=_target, daemon=True)
        action.status = ComputerStatus.RUNNING
        worker.start()
        worker.join(timeout=timeout)
        if worker.is_alive():
            action.status = ComputerStatus.TIMEOUT
            action.error = f"action timed out after {timeout}s"
            action.completed_at = action.created_at
            self._finish(action)
            return self._result_from(action)
        if "error" in box:
            exc: ComputerError = box["error"]
            action.status = ComputerStatus.FAILED
            action.error = exc.message
            action.metadata["error_code"] = exc.code.value
            action.completed_at = action.created_at
            self._finish(action)
            return self._result_from(action)
        action.status = ComputerStatus.COMPLETED
        action.completed_at = action.created_at
        action.metadata["result"] = box.get("result")
        action.metadata["duration_seconds"] = time.monotonic() - (
            action.started_at or action.created_at
        ).timestamp()
        self._finish(action)
        return self._result_from(action)

    def _dispatch_sync(self, action_type: str, arguments: dict[str, Any]) -> dict[str, Any]:
        handler = getattr(self.backend, action_type, None)
        if handler is None:
            raise ComputerError(
                ComputerErrorCode.INVALID_ACTION, f"unknown action: {action_type!r}"
            )
        # Coordinate safety: reject out-of-bounds / negative coordinates instead
        # of silently clamping (clamping could cause accidental clicks).
        if action_type in ("move_mouse", "click", "double_click", "scroll"):
            if "x" in arguments or "y" in arguments:
                screen = self._screen_provider()
                self.policy.validate_coordinates(
                    arguments.get("x"), arguments.get("y"), screen
                )
        if action_type == "type_text":
            arguments = dict(arguments)
            text = self.policy.validate_text(
                arguments.get("text", ""),
                getattr(self, "max_type_text_length", 2000),
            )
            arguments["text"] = text
        if action_type in ("key_press", "hotkey"):
            arguments = dict(arguments)
            if action_type == "key_press":
                keys = self.policy.validate_keys([arguments.get("key", "")])
                arguments["key"] = keys[0]
            else:
                keys = self.policy.validate_keys(arguments.get("keys", []))
                arguments["keys"] = keys
                high, name = self.policy.hotkey_risk_check(keys)
                if high:
                    # Hotkey may run even when risk-checked; risk policy hooks
                    # here for Phase 8.6 confirmation.
                    raise ComputerError(
                        ComputerErrorCode.CONFIRMATION_REQUIRED,
                        f"high-risk hotkey {name!r} requires confirmation",
                    )
        return handler(**arguments)

    def _finish(self, action: ComputerAction) -> None:
        self._emit(_STATUS_TO_EVENT[action.status], action)
        self._record_temporal(action, action.status.value)

    def _result_from(self, action: ComputerAction) -> ComputerActionResult:
        return ComputerActionResult(
            status=action.status,
            action_id=action.action_id,
            action_type=action.action_type,
            error=action.error,
            error_code=action.metadata.get("error_code"),
            metadata=action.metadata,
        )

    def _emit(self, event_type: EventType, action: ComputerAction) -> None:
        if self.bus is None:
            return
        payload = {
            "action_id": action.action_id,
            "action_type": action.action_type,
            "status": action.status.value,
            "error": action.error,
        }
        if action.metadata.get("duration_seconds") is not None:
            payload["duration"] = action.metadata["duration_seconds"]
        self.bus.publish(Event(event_type, "computer", payload))

    def _record_temporal(self, action: ComputerAction, status: str) -> None:
        if self.temporal is None:
            return
        try:
            self.temporal.record_tool_activity(
                "computer_action",
                status,
                duration_seconds=action.metadata.get("duration_seconds"),
                call_id=action.action_id,
            )
        except Exception:  # noqa: BLE001
            pass
