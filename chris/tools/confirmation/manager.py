"""ConfirmationManager: creates, tracks and decides confirmation requests.

State machine (all transitions are validated; illegal ones raise):
  PENDING -> APPROVED -> CONSUMED
  PENDING -> DENIED
  PENDING -> EXPIRED   (TTL)
  PENDING -> CANCELLED

Decisions are one-shot: an APPROVED request can only be consumed once; after
DENIED/EXPIRED/CANCELLED/CONSUMED no further transition to APPROVED is allowed.

Thread-safe (multiple pending confirmations may coexist). In-memory only:
restart invalidates all pending requests. Confirmation payload never carries
sensitive argument text into events/temporal - only the safe summary.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta
from typing import Any

from chris.events import Event, EventBus, EventType, utcnow
from chris.tools.confirmation.errors import ConfirmationError, ConfirmationErrorCode
from chris.tools.confirmation.models import ConfirmationRequest, ConfirmationStatus

_STATUS_TO_EVENT = {
    ConfirmationStatus.PENDING: EventType.CONFIRMATION_REQUESTED,
    ConfirmationStatus.APPROVED: EventType.CONFIRMATION_APPROVED,
    ConfirmationStatus.DENIED: EventType.CONFIRMATION_DENIED,
    ConfirmationStatus.EXPIRED: EventType.CONFIRMATION_EXPIRED,
    ConfirmationStatus.CANCELLED: EventType.CONFIRMATION_CANCELLED,
    ConfirmationStatus.CONSUMED: EventType.CONFIRMATION_CONSUMED,
}

# allowed transitions
_TRANSITIONS: dict[ConfirmationStatus, frozenset[ConfirmationStatus]] = {
    ConfirmationStatus.PENDING: frozenset(
        {
            ConfirmationStatus.APPROVED,
            ConfirmationStatus.DENIED,
            ConfirmationStatus.EXPIRED,
            ConfirmationStatus.CANCELLED,
        }
    ),
    ConfirmationStatus.APPROVED: frozenset({ConfirmationStatus.CONSUMED}),
    ConfirmationStatus.DENIED: frozenset(),
    ConfirmationStatus.EXPIRED: frozenset(),
    ConfirmationStatus.CANCELLED: frozenset(),
    ConfirmationStatus.CONSUMED: frozenset(),
}


class ConfirmationManager:
    def __init__(
        self,
        bus: EventBus | None = None,
        temporal=None,
        default_ttl_seconds: float = 60.0,
        max_ttl_seconds: float = 300.0,
        clock=None,
    ) -> None:
        self.bus = bus
        self.temporal = temporal
        self.default_ttl = default_ttl_seconds
        self.max_ttl = max_ttl_seconds
        self._clock = clock or utcnow
        self._requests: dict[str, ConfirmationRequest] = {}
        self._lock = threading.Lock()

    # -- creation -------------------------------------------------------

    def create_request(
        self,
        *,
        call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        risk_level: str,
        reason: str,
        source: str = "llm",
        ttl_seconds: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ConfirmationRequest:
        ttl = ttl_seconds if ttl_seconds is not None else self.default_ttl
        ttl = max(0.0, min(ttl, self.max_ttl))
        now = self._clock()
        request = ConfirmationRequest(
            call_id=call_id,
            tool_name=tool_name,
            arguments=dict(arguments),
            risk_level=risk_level,
            reason=reason,
            source=source,
            status=ConfirmationStatus.PENDING,
            created_at=now,
            expires_at=now + timedelta(seconds=ttl),
            metadata=metadata or {},
        )
        with self._lock:
            self._requests[request.confirmation_id] = request
        self._emit(ConfirmationStatus.PENDING, request)
        self._record_temporal(request, "REQUESTED")
        return request

    # -- queries --------------------------------------------------------

    def get_request(self, confirmation_id: str) -> ConfirmationRequest | None:
        with self._lock:
            return self._requests.get(confirmation_id)

    def list_pending(self) -> list[ConfirmationRequest]:
        with self._lock:
            return [
                r for r in self._requests.values() if r.status is ConfirmationStatus.PENDING
            ]

    # -- decisions ------------------------------------------------------

    def approve(self, confirmation_id: str, decided_by: str = "user") -> ConfirmationRequest:
        return self._transition(confirmation_id, ConfirmationStatus.APPROVED, decided_by)

    def deny(self, confirmation_id: str, decided_by: str = "user") -> ConfirmationRequest:
        return self._transition(confirmation_id, ConfirmationStatus.DENIED, decided_by)

    def cancel(self, confirmation_id: str, decided_by: str = "user") -> ConfirmationRequest:
        return self._transition(confirmation_id, ConfirmationStatus.CANCELLED, decided_by)

    def consume(self, confirmation_id: str) -> ConfirmationRequest:
        return self._transition(confirmation_id, ConfirmationStatus.CONSUMED, "system")

    def expire_pending(self) -> list[ConfirmationRequest]:
        """Move all expired PENDING requests to EXPIRED. Returns the expired list."""
        expired: list[ConfirmationRequest] = []
        with self._lock:
            for request in self._requests.values():
                if request.status is ConfirmationStatus.PENDING and self._is_expired(request):
                    request.status = ConfirmationStatus.EXPIRED
                    request.decided_at = self._clock()
                    request.decided_by = "system"
                    expired.append(request)
        for request in expired:
            self._emit(ConfirmationStatus.EXPIRED, request)
            self._record_temporal(request, "EXPIRED")
        return expired

    # -- internals ------------------------------------------------------

    def _is_expired(self, request: ConfirmationRequest) -> bool:
        return self._clock() >= request.expires_at

    def _transition(
        self, confirmation_id: str, target: ConfirmationStatus, decided_by: str
    ) -> ConfirmationRequest:
        with self._lock:
            request = self._requests.get(confirmation_id)
            if request is None:
                raise ConfirmationError(
                    ConfirmationErrorCode.CONFIRMATION_NOT_FOUND,
                    f"no confirmation {confirmation_id!r}",
                )
            if target is not ConfirmationStatus.EXPIRED and self._is_expired(request):
                request.status = ConfirmationStatus.EXPIRED
                request.decided_at = self._clock()
                request.decided_by = "system"
                self._emit(ConfirmationStatus.EXPIRED, request)
                self._record_temporal(request, "EXPIRED")
                raise ConfirmationError(
                    ConfirmationErrorCode.CONFIRMATION_EXPIRED,
                    f"confirmation {confirmation_id!r} has expired",
                )
            if target not in _TRANSITIONS[request.status]:
                raise ConfirmationError(
                    ConfirmationErrorCode.CONFIRMATION_INVALID_TRANSITION,
                    f"cannot move confirmation {confirmation_id!r} from "
                    f"{request.status.value} to {target.value}",
                )
            request.status = target
            request.decided_at = self._clock()
            request.decided_by = decided_by

        self._emit(target, request)
        self._record_temporal(request, target.value)
        return request

    def _emit(self, status: ConfirmationStatus, request: ConfirmationRequest) -> None:
        if self.bus is None:
            return
        event_type = _STATUS_TO_EVENT.get(status)
        if event_type is None:
            return
        payload = request.safe_summary()
        payload["reason"] = request.reason
        self.bus.publish(Event(event_type, "confirmation", payload))

    def _record_temporal(self, request: ConfirmationRequest, status: str) -> None:
        if self.temporal is None:
            return
        try:
            self.temporal.record_tool_activity(
                "confirmation",
                status,
                duration_seconds=None,
                call_id=request.confirmation_id,
            )
        except Exception:  # noqa: BLE001
            pass
