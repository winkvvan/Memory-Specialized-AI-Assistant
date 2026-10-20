"""Resource limits for filesystem tools (config-driven, shared across tools).

Defaults mirror the Phase 8.3 spec; all values are overridable through the
`tools.filesystem` config section so no limit is hardcoded in multiple tools.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DEFAULT_MAX_READ_BYTES = 1024 * 1024  # 1 MB
DEFAULT_MAX_WRITE_BYTES = 1024 * 1024  # 1 MB
DEFAULT_MAX_RESULTS = 200
DEFAULT_MAX_DIRECTORY_ENTRIES = 500
DEFAULT_MAX_SEARCH_DEPTH = 20


@dataclass(frozen=True)
class FilesystemLimits:
    max_read_bytes: int = DEFAULT_MAX_READ_BYTES
    max_write_bytes: int = DEFAULT_MAX_WRITE_BYTES
    max_results: int = DEFAULT_MAX_RESULTS
    max_directory_entries: int = DEFAULT_MAX_DIRECTORY_ENTRIES
    max_search_depth: int = DEFAULT_MAX_SEARCH_DEPTH

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "FilesystemLimits":
        raw = raw or {}
        return cls(
            max_read_bytes=int(raw.get("max_read_bytes", DEFAULT_MAX_READ_BYTES)),
            max_write_bytes=int(raw.get("max_write_bytes", DEFAULT_MAX_WRITE_BYTES)),
            max_results=int(raw.get("max_results", DEFAULT_MAX_RESULTS)),
            max_directory_entries=int(
                raw.get("max_directory_entries", DEFAULT_MAX_DIRECTORY_ENTRIES)
            ),
            max_search_depth=int(raw.get("max_search_depth", DEFAULT_MAX_SEARCH_DEPTH)),
        )
