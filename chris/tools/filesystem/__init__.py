"""Filesystem tool subsystem for Chris (Phase 8.3).

Safe, controlled, auditable local file operations confined to a workspace
boundary. Every operation goes through the Tool Core (Registry -> Executor ->
Permission -> File Tool -> Filesystem Adapter), never direct file access from
chat/CLI/GUI. Uses only the stdlib (`os`, `pathlib`, `shutil`) - no new deps,
no guardrail exemption needed (unlike the HTTP search adapters).
"""

from chris.tools.filesystem.errors import FileToolError, FileToolErrorCode
from chris.tools.filesystem.limits import FilesystemLimits
from chris.tools.filesystem.path import WorkspacePathPolicy

__all__ = [
    "FileToolError",
    "FileToolErrorCode",
    "FilesystemLimits",
    "WorkspacePathPolicy",
]
