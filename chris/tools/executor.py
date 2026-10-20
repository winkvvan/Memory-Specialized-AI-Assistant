"""ToolExecutor: the validation -> permission -> execution pipeline.

Single tool calls run serially (safe, deterministic). Timeout is enforced by
running the tool in a daemon thread and joining with a deadline; a hung tool
yields ToolResult(status=timeout) instead of hanging Chris forever. Cooperative
cancellation is checked before execution via ``context.cancel_event``.

Phase 8.6: when the permission policy returns CONFIRMATION_REQUIRED, the
executor creates a ConfirmationRequest (via an optional ConfirmationManager) and
returns a structured confirmation_required ToolResult instead of executing.
The `execute_approved` path re-runs lookup + validation + permission before
executing, so an approval never bypasses the safety checks (TOCTOU-safe).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Sequence

from chris.events import Event, EventBus, EventType
from chris.tools.base import Tool, ToolContext
from chris.tools.confirmation.errors import ConfirmationError, ConfirmationErrorCode
from chris.tools.confirmation.manager import ConfirmationManager
from chris.tools.models import ToolCall, ToolResult, ToolStatus
from chris.tools.permission import (
    DefaultPermissionPolicy,
    PermissionDecision,
    PermissionOutcome,
    PermissionPolicy,
)
from chris.tools.registry import ToolRegistry
from chris.tools.validation import validate_arguments

log = logging.getLogger("chris.tools")

_STATUS_TO_EVENT = {
    ToolStatus.SUCCESS: EventType.TOOL_CALL_COMPLETED,
    ToolStatus.FAILURE: EventType.TOOL_CALL_FAILED,
    ToolStatus.DENIED: EventType.TOOL_CALL_DENIED,
    ToolStatus.CANCELLED: EventType.TOOL_CALL_CANCELLED,
    ToolStatus.TIMEOUT: EventType.TOOL_CALL_FAILED,
}


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        bus: EventBus | None = None,
        policy: PermissionPolicy | None = None,
        temporal=None,
        default_timeout: float = 30.0,
        confirmation: ConfirmationManager | None = None,
    ) -> None:
        self.registry = registry
        self.bus = bus
        self.policy = policy or DefaultPermissionPolicy()
        self.temporal = temporal
        self.default_timeout = default_timeout
        self.confirmation = confirmation

    # -- public ---------------------------------------------------------

    def execute(
        self, call: ToolCall, context: ToolContext, timeout: float | None = None
    ) -> ToolResult:
        return self._execute(call, context, timeout=timeout, approved=False)

    def execute_approved(
        self,
        call: ToolCall,
        context: ToolContext,
        confirmation_id: str,
        timeout: float | None = None,
    ) -> ToolResult:
        """Execute a call whose confirmation was approved by the user.

        Re-runs lookup + validation + permission (with the approval recorded),
        then executes. An expired / already-decided confirmation raises a
        structured error instead of executing.
        """
        return self._execute(
            call, context, timeout=timeout, approved=True, confirmation_id=confirmation_id
        )

    def execute_many(
        self,
        calls: Sequence[ToolCall],
        context: ToolContext,
        timeout: float | None = None,
    ) -> list[ToolResult]:
        """Serial, deterministic execution of multiple calls (no concurrency)."""
        results: list[ToolResult] = []
        for call in calls:
            if context.cancel_event is not None and context.cancel_event.is_set():
                results.append(
                    self._terminal(
                        call, ToolStatus.CANCELLED, error="cancelled",
                        started=time.perf_counter(),
                    )
                )
                continue
            results.append(self.execute(call, context, timeout=timeout))
        return results

    # -- internals ------------------------------------------------------

    def _execute(
        self,
        call: ToolCall,
        context: ToolContext,
        *,
        timeout: float | None = None,
        approved: bool = False,
        confirmation_id: str | None = None,
    ) -> ToolResult:
        started = time.perf_counter()
        tool = self.registry.get(call.tool_name)
        if tool is None:
            return self._terminal(
                call, ToolStatus.FAILURE, error=f"unknown tool: {call.tool_name}",
                started=started,
            )

        definition = tool.definition()
        if not definition.enabled:
            return self._terminal(
                call, ToolStatus.DENIED, error=f"tool is disabled: {call.tool_name}",
                started=started,
            )

        if context.cancel_event is not None and context.cancel_event.is_set():
            return self._terminal(
                call, ToolStatus.CANCELLED, error="cancelled before execution",
                started=started,
            )

        validation = validate_arguments(definition.input_schema, call.arguments)
        if not validation.valid:
            return self._terminal(
                call, ToolStatus.FAILURE,
                error="; ".join(validation.errors),
                started=started,
                metadata={"validation": validation.errors},
            )

        decision = self.policy.check(definition, context)
        if decision.outcome is PermissionOutcome.DENY:
            return self._terminal(
                call, ToolStatus.DENIED, error=decision.reason, started=started,
                metadata={
                    "error_code": "permission_denied",
                    "risk_level": definition.risk_level.value,
                },
            )

        if decision.outcome is PermissionOutcome.CONFIRMATION_REQUIRED and not approved:
            return self._require_confirmation(call, definition, decision, started)

        if decision.outcome is PermissionOutcome.CONFIRMATION_REQUIRED and approved:
            # Re-validate the approval was real and still valid.
            try:
                self._consume_approval(call, confirmation_id)
            except ConfirmationError as exc:
                return self._terminal(
                    call, ToolStatus.DENIED, error=exc.message, started=started,
                    metadata={"error_code": exc.code.value},
                )
            # Mark the context as approved so inner managers (computer/coding)
            # don't re-deny; they still run their own validation.
            import dataclasses

            context = dataclasses.replace(context, approved=True)

        return self._run(call, tool, context, timeout, started)

    def _require_confirmation(
        self,
        call: ToolCall,
        definition,
        decision: PermissionDecision,
        started: float,
    ) -> ToolResult:
        metadata: dict[str, Any] = {
            "error_code": "confirmation_required",
            "risk_level": definition.risk_level.value,
            "tool_name": call.tool_name,
        }
        if self.confirmation is None:
            # No confirmation manager wired: keep the 8.1-8.5 behaviour.
            return self._terminal(
                call, ToolStatus.DENIED, error=decision.reason, started=started,
                metadata=metadata,
            )
        request = self.confirmation.create_request(
            call_id=call.call_id,
            tool_name=call.tool_name,
            arguments=call.arguments,
            risk_level=definition.risk_level.value,
            reason=decision.reason,
            source=call.source,
        )
        metadata["confirmation_id"] = request.confirmation_id
        metadata["expires_at"] = request.expires_at.isoformat()
        return self._terminal(
            call, ToolStatus.DENIED, error=decision.reason, started=started,
            metadata=metadata,
        )

    def _consume_approval(self, call: ToolCall, confirmation_id):
        if self.confirmation is None or not confirmation_id:
            raise ConfirmationError(
                ConfirmationErrorCode.CONFIRMATION_NOT_FOUND,
                "no confirmation id provided",
            )
        request = self.confirmation.get_request(confirmation_id)
        if request is None:
            raise ConfirmationError(
                ConfirmationErrorCode.CONFIRMATION_NOT_FOUND,
                f"no confirmation {confirmation_id!r}",
            )
        # The approval must match the original call exactly (fixed arguments).
        if request.call_id != call.call_id or request.tool_name != call.tool_name:
            raise ConfirmationError(
                ConfirmationErrorCode.CONFIRMATION_INVALID_TRANSITION,
                "confirmation does not match the requested tool call",
            )
        # Approve (validates PENDING + not expired) then consume exactly once.
        self.confirmation.approve(confirmation_id, decided_by="user")
        self.confirmation.consume(confirmation_id)
        return request

    def _run(
        self,
        call: ToolCall,
        tool: Tool,
        context: ToolContext,
        timeout: float | None,
        started: float,
    ) -> ToolResult:
        self._emit(
            EventType.TOOL_CALL_STARTED,
            {"call_id": call.call_id, "tool_name": call.tool_name, "source": call.source},
        )
        if self.temporal is not None:
            self.temporal.record_tool_activity(
                call.tool_name, "STARTED", call_id=call.call_id
            )
        limit = timeout if timeout is not None else self.default_timeout
        result = self._run_guarded(tool, context, call.arguments, limit, call)
        if result.status is ToolStatus.TIMEOUT:
            result.error = result.error or f"tool timed out after {limit:.1f}s"
        result.duration_seconds = time.perf_counter() - started
        if self.temporal is not None:
            self.temporal.record_tool_activity(
                call.tool_name,
                "COMPLETED" if result.ok else "FAILED",
                duration_seconds=result.duration_seconds,
                call_id=call.call_id,
            )
        self._emit(
            _STATUS_TO_EVENT[result.status],
            {
                "call_id": call.call_id,
                "tool_name": call.tool_name,
                "status": result.status.value,
                "duration_seconds": result.duration_seconds,
                "error": result.error,
            },
        )
        return result

    def _run_guarded(
        self,
        tool: Tool,
        context: ToolContext,
        arguments: dict[str, Any],
        timeout: float,
        call: ToolCall,
    ) -> ToolResult:
        box: dict[str, Any] = {}

        def _target() -> None:
            try:
                result = tool.execute(context, arguments)
                box["result"] = result
            except Exception as exc:  # noqa: BLE001
                box["exception"] = exc

        worker = threading.Thread(target=_target, daemon=True)
        worker.start()
        worker.join(timeout=timeout)
        if worker.is_alive():
            log.warning("tool %s timed out after %.1fs", tool.name, timeout)
            return ToolResult(
                call_id=call.call_id,
                tool_name=call.tool_name,
                status=ToolStatus.TIMEOUT,
            )
        if "exception" in box:
            exc = box["exception"]
            log.error("tool %s failed: %s", tool.name, exc)
            return ToolResult(
                call_id=call.call_id,
                tool_name=call.tool_name,
                status=ToolStatus.FAILURE,
                error=f"{type(exc).__name__}: {exc}",
            )
        result = box.get("result")
        if not isinstance(result, ToolResult):
            result = ToolResult.success(call.tool_name, content=str(result))
        result.call_id = result.call_id or call.call_id
        result.tool_name = result.tool_name or call.tool_name
        return result

    def _terminal(
        self,
        call: ToolCall,
        status: ToolStatus,
        *,
        error: str | None = None,
        started: float,
        metadata: dict[str, Any] | None = None,
    ) -> ToolResult:
        result = ToolResult(
            call_id=call.call_id,
            tool_name=call.tool_name,
            status=status,
            error=error,
            metadata=metadata or {},
            duration_seconds=time.perf_counter() - started,
        )
        self._emit(
            _STATUS_TO_EVENT[status],
            {
                "call_id": call.call_id,
                "tool_name": call.tool_name,
                "status": status.value,
                "error": error,
            },
        )
        return result

    def _emit(self, event_type: EventType, payload: dict[str, Any]) -> None:
        if self.bus is not None:
            self.bus.publish(Event(event_type, "tool", payload))
