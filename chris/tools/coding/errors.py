"""Unified coding task error model.

Errors are never Python tracebacks in front of the LLM - they map to stable
codes and friendly messages that ride on the ToolResult.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class CodingErrorCode(str, Enum):
    OPENCODE_NOT_INSTALLED = "opencode_not_installed"
    PROJECT_CONTEXT_REQUIRED = "project_context_required"
    PROJECT_NOT_FOUND = "project_not_found"
    PROJECT_OUTSIDE_WORKSPACE = "project_outside_workspace"
    PROJECT_BUSY = "project_busy"
    PERMISSION_DENIED = "permission_denied"
    CONFIRMATION_REQUIRED = "confirmation_required"
    TIMEOUT = "operation_timeout"
    CANCELLED = "operation_cancelled"
    INVALID_ARGUMENT = "invalid_argument"
    NOT_FOUND = "task_not_found"
    EXECUTION_ERROR = "execution_error"
    PARSE_ERROR = "parse_error"
    SENSITIVE_ENV_FILTERED = "sensitive_environment_filtered"
    UNKNOWN = "unknown"


class CodingError(Exception):
    """Structured coding operation failure."""

    def __init__(
        self,
        code: CodingErrorCode,
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
