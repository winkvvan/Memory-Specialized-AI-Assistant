"""Unified computer action error model.

Backend exceptions (e.g. pyautogui/ctypes failures) are never surfaced to the
LLM as tracebacks - they map to stable codes and friendly messages.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class ComputerErrorCode(str, Enum):
    BACKEND_ERROR = "backend_error"
    BACKEND_UNAVAILABLE = "backend_unavailable"
    INVALID_ARGUMENT = "invalid_argument"
    INVALID_COORDINATES = "invalid_coordinates"
    TEXT_TOO_LONG = "text_too_long"
    INVALID_KEY = "invalid_key"
    TOO_MANY_KEYS = "too_many_keys"
    INVALID_ACTION = "invalid_action"
    SEQUENCE_TOO_LONG = "sequence_too_long"
    TIMEOUT_LIMIT_EXCEEDED = "timeout_limit_exceeded"
    PERMISSION_DENIED = "permission_denied"
    CONFIRMATION_REQUIRED = "confirmation_required"
    OPERATION_CANCELLED = "operation_cancelled"
    OPERATION_TIMEOUT = "operation_timeout"
    NOT_FOUND = "action_not_found"
    CONCURRENT_ACTION = "concurrent_action"
    UNKNOWN = "unknown"


class ComputerError(Exception):
    """Structured computer action failure."""

    def __init__(
        self,
        code: ComputerErrorCode,
        message: str,
        *,
        detail: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code.value, "message": self.message}
