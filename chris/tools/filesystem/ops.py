"""Filesystem operations behind the workspace boundary.

Everything here goes through :class:`WorkspacePathPolicy` first. Operations are
synchronous (matching the Chris core), size/count-limited, cancellation-aware
(checked on recursive traversals), and never log file contents. Writes are
atomic (temp file + os.replace) so a crash never leaves a half-written target.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from chris.tools.filesystem.errors import FileToolError, FileToolErrorCode
from chris.tools.filesystem.limits import FilesystemLimits
from chris.tools.filesystem.path import WorkspacePathPolicy

SENSITIVE_PATTERNS = (".env", "*.key", "*.pem", "credentials", "secret")


def _ts(seconds: float | None) -> str:
    if not seconds:
        return ""
    return datetime.fromtimestamp(seconds).isoformat()


def _is_binary(data: bytes) -> bool:
    """Heuristic: NUL bytes strongly suggest a binary file."""
    return b"\x00" in data


class Filesystem:
    def __init__(
        self,
        policy: WorkspacePathPolicy,
        limits: FilesystemLimits | None = None,
        cancel_event: threading.Event | None = None,
        reject_sensitive: bool = False,
    ) -> None:
        self.policy = policy
        self.limits = limits or FilesystemLimits()
        self.cancel_event = cancel_event
        # Sensitive-file rejection is an explicit, configurable policy (off by
        # default: the workspace boundary is the primary guard, and dev projects
        # legitimately read .env). This is the extension point where a future
        # Phase 8.6 confirmation flow plugs in.
        self.reject_sensitive = reject_sensitive

    def _check_cancel(self) -> None:
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise FileToolError(
                FileToolErrorCode.OPERATION_CANCELLED, "operation cancelled"
            )

    # -- info -----------------------------------------------------------

    def file_info(self, user_path: str) -> dict[str, Any]:
        path = self.policy.resolve(user_path)
        if not path.exists():
            raise FileToolError(FileToolErrorCode.FILE_NOT_FOUND, "file not found")
        stat = path.stat()
        kind = "directory" if path.is_dir() else "file"
        return {
            "name": path.name,
            "path": _relative_display(self.policy, path),
            "type": kind,
            "size": stat.st_size if kind == "file" else None,
            "created_at": _ts(stat.st_ctime),
            "modified_at": _ts(stat.st_mtime),
            "extension": path.suffix.lstrip(".") if path.suffix else "",
        }

    # -- directory listing ---------------------------------------------

    def list_directory(self, user_path: str) -> dict[str, Any]:
        path = self.policy.resolve(user_path)
        if not path.exists():
            raise FileToolError(FileToolErrorCode.DIRECTORY_NOT_FOUND, "directory not found")
        if not path.is_dir():
            raise FileToolError(FileToolErrorCode.NOT_A_DIRECTORY, "path is not a directory")

        entries: list[dict[str, Any]] = []
        try:
            children = sorted(path.iterdir(), key=lambda p: p.name.casefold())
        except OSError as exc:
            raise FileToolError(FileToolErrorCode.IO_ERROR, "cannot read directory", detail=str(exc)) from exc

        for child in children:
            self._check_cancel()
            if len(entries) >= self.limits.max_directory_entries:
                break
            try:
                stat = child.stat()
            except OSError:
                continue
            kind = "directory" if child.is_dir() else "file"
            entries.append(
                {
                    "name": child.name,
                    "type": kind,
                    "size": stat.st_size if kind == "file" else None,
                    "modified_at": _ts(stat.st_mtime),
                }
            )
        return {
            "path": _relative_display(self.policy, path),
            "count": len(entries),
            "truncated": len(entries) >= self.limits.max_directory_entries,
            "entries": entries,
        }

    # -- search ---------------------------------------------------------

    def find_files(
        self,
        user_path: str,
        pattern: str,
        recursive: bool,
        max_results: int | None = None,
        max_depth: int | None = None,
    ) -> dict[str, Any]:
        root = self.policy.resolve(user_path)
        if not root.exists():
            raise FileToolError(FileToolErrorCode.DIRECTORY_NOT_FOUND, "directory not found")
        if not root.is_dir():
            raise FileToolError(FileToolErrorCode.NOT_A_DIRECTORY, "path is not a directory")

        import fnmatch

        limit = min(max_results or self.limits.max_results, self.limits.max_results)
        depth_limit = self.limits.max_search_depth
        if max_depth is not None:
            depth_limit = min(int(max_depth), depth_limit)
        matches: list[dict[str, Any]] = []

        def _walk(directory: Path, depth: int) -> None:
            self._check_cancel()
            if depth > depth_limit:
                return
            if len(matches) >= limit:
                return
            try:
                children = sorted(directory.iterdir(), key=lambda p: p.name.casefold())
            except OSError:
                return
            for child in children:
                self._check_cancel()
                if len(matches) >= limit:
                    return
                if child.is_dir():
                    if recursive:
                        # Only descend into directories we can safely resolve inside
                        # the workspace; skip symlinks/junctions that escape.
                        if self.policy.is_within(child):
                            _walk(child, depth + 1)
                elif fnmatch.fnmatch(child.name, pattern):
                    try:
                        stat = child.stat()
                    except OSError:
                        continue
                    matches.append(
                        {
                            "name": child.name,
                            "path": _relative_display(self.policy, child),
                            "size": stat.st_size,
                            "modified_at": _ts(stat.st_mtime),
                        }
                    )

        _walk(root, 0)
        return {
            "pattern": pattern,
            "count": len(matches),
            "truncated": len(matches) >= limit,
            "matches": matches,
        }

    # -- read -----------------------------------------------------------

    def read_file(
        self,
        user_path: str,
        encoding: str = "utf-8",
        max_bytes: int | None = None,
    ) -> dict[str, Any]:
        path = self.policy.resolve(user_path)
        if not path.exists() or not path.is_file():
            raise FileToolError(FileToolErrorCode.FILE_NOT_FOUND, "file not found")

        self._reject_sensitive(path)

        limit = min(max_bytes or self.limits.max_read_bytes, self.limits.max_read_bytes)
        actual = path.stat().st_size
        truncated = actual > limit
        try:
            with open(path, "rb") as handle:
                data = handle.read(limit)
        except OSError as exc:
            raise FileToolError(FileToolErrorCode.IO_ERROR, "cannot read file", detail=str(exc)) from exc

        if _is_binary(data):
            raise FileToolError(
                FileToolErrorCode.BINARY_FILE,
                "binary file not supported by read_file",
                detail={"extension": path.suffix},
            )

        try:
            text = data.decode(encoding)
        except UnicodeDecodeError as exc:
            raise FileToolError(
                FileToolErrorCode.INVALID_ENCODING,
                f"cannot decode file as {encoding}",
                detail=str(exc),
            ) from exc

        return {
            "name": path.name,
            "path": _relative_display(self.policy, path),
            "size": actual,
            "encoding": encoding,
            "truncated": truncated,
            "content": text,
        }

    # -- write ----------------------------------------------------------

    def write_file(
        self,
        user_path: str,
        content: str,
        encoding: str = "utf-8",
        overwrite: bool = False,
        max_bytes: int | None = None,
    ) -> dict[str, Any]:
        path = self.policy.resolve(user_path)
        self._reject_sensitive(path)

        data = content.encode(encoding, errors="replace")
        limit = min(max_bytes or self.limits.max_write_bytes, self.limits.max_write_bytes)
        if len(data) > limit:
            raise FileToolError(
                FileToolErrorCode.FILE_TOO_LARGE,
                f"content exceeds {limit} byte write limit",
            )

        if path.exists():
            if not path.is_file():
                raise FileToolError(FileToolErrorCode.IS_A_DIRECTORY, "target is a directory")
            if not overwrite:
                raise FileToolError(
                    FileToolErrorCode.OVERWRITE_NOT_ALLOWED,
                    "target exists; pass overwrite=true to replace it",
                )

        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=".chris-write-")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, path)
        except OSError as exc:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise FileToolError(FileToolErrorCode.IO_ERROR, "cannot write file", detail=str(exc)) from exc

        return {
            "name": path.name,
            "path": _relative_display(self.policy, path),
            "size": len(data),
            "encoding": encoding,
            "overwritten": overwrite,
        }

    # -- directories ----------------------------------------------------

    def create_directory(self, user_path: str) -> dict[str, Any]:
        path = self.policy.resolve(user_path)
        if path.exists():
            if not path.is_dir():
                raise FileToolError(FileToolErrorCode.TARGET_EXISTS, "target exists and is not a directory")
            return {
                "name": path.name,
                "path": _relative_display(self.policy, path),
                "created": False,
                "message": "directory already exists",
            }
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise FileToolError(FileToolErrorCode.IO_ERROR, "cannot create directory", detail=str(exc)) from exc
        return {
            "name": path.name,
            "path": _relative_display(self.policy, path),
            "created": True,
        }

    # -- delete ---------------------------------------------------------

    def delete_file(self, user_path: str) -> dict[str, Any]:
        path = self.policy.resolve(user_path)
        if not path.exists():
            raise FileToolError(FileToolErrorCode.FILE_NOT_FOUND, "file not found")
        if path.is_dir():
            raise FileToolError(
                FileToolErrorCode.IS_A_DIRECTORY,
                "delete_file only removes files, not directories",
            )
        try:
            os.remove(path)
        except OSError as exc:
            raise FileToolError(FileToolErrorCode.IO_ERROR, "cannot delete file", detail=str(exc)) from exc
        return {
            "name": path.name,
            "path": _relative_display(self.policy, path),
            "deleted": True,
        }

    # -- move / copy ----------------------------------------------------

    def move_file(self, source: str, destination: str, overwrite: bool = False) -> dict[str, Any]:
        src = self.policy.resolve(source)
        dst = self.policy.resolve(destination)
        if not src.exists():
            raise FileToolError(FileToolErrorCode.FILE_NOT_FOUND, "source not found")
        if dst.exists() and not overwrite:
            raise FileToolError(
                FileToolErrorCode.OVERWRITE_NOT_ALLOWED,
                "destination exists; pass overwrite=true to replace it",
            )
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(src), str(dst))
        except OSError as exc:
            raise FileToolError(FileToolErrorCode.IO_ERROR, "cannot move file", detail=str(exc)) from exc
        return {
            "source": _relative_display(self.policy, src),
            "destination": _relative_display(self.policy, dst),
            "moved": True,
        }

    def copy_file(self, source: str, destination: str, overwrite: bool = False) -> dict[str, Any]:
        src = self.policy.resolve(source)
        dst = self.policy.resolve(destination)
        if not src.exists():
            raise FileToolError(FileToolErrorCode.FILE_NOT_FOUND, "source not found")
        if dst.exists() and not overwrite:
            raise FileToolError(
                FileToolErrorCode.OVERWRITE_NOT_ALLOWED,
                "destination exists; pass overwrite=true to replace it",
            )
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(str(src), str(dst))
        except OSError as exc:
            raise FileToolError(FileToolErrorCode.IO_ERROR, "cannot copy file", detail=str(exc)) from exc
        return {
            "source": _relative_display(self.policy, src),
            "destination": _relative_display(self.policy, dst),
            "copied": True,
        }

    # -- sensitive files ------------------------------------------------

    def _reject_sensitive(self, path: Path) -> None:
        if not self.reject_sensitive:
            return
        import fnmatch

        name = path.name
        for pattern in SENSITIVE_PATTERNS:
            if fnmatch.fnmatch(name, pattern) or name.startswith(pattern):
                raise FileToolError(
                    FileToolErrorCode.SENSITIVE_FILE,
                    f"file is protected by sensitive-file policy: {name!r}",
                    detail={"name": name},
                )


def _relative_display(policy: WorkspacePathPolicy, path: Path) -> str:
    """Display path relative to the workspace (or the name for the root)."""
    try:
        return path.resolve().relative_to(policy._canonical_root).as_posix()
    except (ValueError, OSError):
        return path.name
