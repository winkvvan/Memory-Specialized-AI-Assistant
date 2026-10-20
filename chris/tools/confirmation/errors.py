"""Confirmation error model."""

from __future__ import annotations

from enum import Enum
from typing import Any


class ConfirmationErrorCode(str, Enum):
    CONFIRMATION_REQUIRED = "confirmation_required"
    CONFIRMATION_NOT_FOUND = "confirmation_not_found"
    CONFIRMATION_EXPIRED = "confirmation_expired"
    CONFIRMATION_ALREADY_DECIDED = "confirmation_already_decided"
    CONFIRMATION_ALREADY_CONSUMED = "confirmation_already_consumed"
    CONFIRMATION_INVALID_TRANSITION = "confirmation_invalid_transition"
    CONFIRMATION_CANCELLED = "confirmation_cancelled"
    CONFIRMATION_EXECUTION_FAILED = "confirmation_execution_failed"


class ConfirmationError(Exception):
    def __init__(self, code: ConfirmationErrorCode, message: str, *, detail: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code.value, "message": self.message}
