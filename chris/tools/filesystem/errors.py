"""Unified file tool error model.

Python tracebacks are never returned to the LLM. Every failure maps to a stable
error code; the friendly message (and the error_code) rides on the ToolResult.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class FileToolErrorCode(str, Enum):
    PATH_OUTSIDE_WORKSPACE = "path_outside_workspace"
    FILE_NOT_FOUND = "file_not_found"
    PERMISSION_DENIED = "permission_denied"
    FILE_TOO_LARGE = "file_too_large"
    BINARY_FILE = "binary_file_not_supported"
    INVALID_ENCODING = "invalid_encoding"
    INVALID_PATH = "invalid_path"
    DIRECTORY_NOT_FOUND = "directory_not_found"
    TARGET_EXISTS = "target_exists"
    OVERWRITE_NOT_ALLOWED = "overwrite_not_allowed"
    CONFIRMATION_REQUIRED = "confirmation_required"
    OPERATION_CANCELLED = "operation_cancelled"
    OPERATION_TIMEOUT = "operation_timeout"
    IO_ERROR = "io_error"
    NOT_A_DIRECTORY = "not_a_directory"
    IS_A_DIRECTORY = "is_a_directory"
    TOO_MANY_RESULTS = "too_many_results"
    SENSITIVE_FILE = "sensitive_file"
    MAX_DEPTH_EXCEEDED = "max_depth_exceeded"


class FileToolError(Exception):
    """Structured, LLM-safe file operation failure."""

    def __init__(
        self,
        code: FileToolErrorCode,
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
