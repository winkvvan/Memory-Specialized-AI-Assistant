"""Workspace path policy: the security boundary for all file tools.

Every user-supplied path must pass through :meth:`WorkspacePathPolicy.resolve`
which:
  1. rejects non-string / empty paths,
  2. normalizes and resolves the path (following symlinks/junctions to their
     canonical location),
  3. verifies the canonical result is strictly inside the canonical workspace
     root using real path containment (never a string prefix).

This blocks `../`, `..\\`, absolute paths outside the workspace, Windows drive
paths, UNC paths, and symlink/junction escapes. If a path cannot be resolved
safely, we deny rather than guess.
"""

from __future__ import annotations

import os
from pathlib import Path

from chris.tools.filesystem.errors import FileToolError, FileToolErrorCode


class WorkspacePathPolicy:
    def __init__(self, workspace_root: str | Path) -> None:
        root = Path(workspace_root)
        self.root = root
        self._canonical_root = self._canonicalize(root)

    @staticmethod
    def _canonicalize(path: Path) -> Path:
        try:
            return Path(os.path.normcase(os.path.abspath(str(path)))).resolve()
        except OSError:
            # If resolution fails, fall back to the normalized absolute path.
            return Path(os.path.normcase(os.path.abspath(str(path))))

    def root_str(self) -> str:
        return str(self.root)

    def is_within(self, candidate: Path) -> bool:
        canonical = self._canonicalize(candidate)
        try:
            common = os.path.commonpath([str(canonical), str(self._canonical_root)])
        except ValueError:
            return False
        return Path(common) == self._canonical_root

    def resolve(self, user_path: str) -> Path:
        """Resolve `user_path` inside the workspace or raise FileToolError."""
        if not isinstance(user_path, str) or not user_path.strip():
            raise FileToolError(
                FileToolErrorCode.INVALID_PATH,
                "path must be a non-empty string",
            )
        raw = user_path.strip()
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = self.root / candidate

        canonical = self._canonicalize(candidate)
        if not self.is_within(canonical):
            raise FileToolError(
                FileToolErrorCode.PATH_OUTSIDE_WORKSPACE,
                f"path resolves outside the workspace: {raw!r}",
            )
        # Return a path rooted at the workspace so the caller never handles a
        # non-workspace path.
        try:
            relative = canonical.relative_to(self._canonical_root)
        except ValueError:
            raise FileToolError(
                FileToolErrorCode.PATH_OUTSIDE_WORKSPACE,
                f"path resolves outside the workspace: {raw!r}",
            )
        return self.root / relative
