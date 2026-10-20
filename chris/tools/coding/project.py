"""Project resolution: map a user-provided project reference to a concrete
project_root inside the workspace, and derive a minimal ProjectContext.

Reuses the 8.3 WorkspacePathPolicy for the boundary - there is exactly one path
policy in the codebase, not a second ad-hoc one. Project references are either
workspace-relative paths or workspace-relative project names. If nothing can be
resolved unambiguously, we return project_context_required instead of guessing.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from chris.tools.coding.errors import CodingError, CodingErrorCode
from chris.tools.coding.models import ProjectContext
from chris.tools.filesystem.path import WorkspacePathPolicy

# marker file -> (language, framework)
_MARKERS = [
    ("pyproject.toml", "python", "python"),
    ("requirements.txt", "python", "python"),
    ("setup.py", "python", "python"),
    ("package.json", "javascript", "node"),
    ("tsconfig.json", "typescript", "node"),
    ("CMakeLists.txt", "cpp", "cmake"),
    ("Cargo.toml", "rust", "cargo"),
    ("go.mod", "go", "go"),
    ("pom.xml", "java", "maven"),
    ("build.gradle", "java", "gradle"),
    (".csproj", "csharp", "dotnet"),
]


class ProjectResolver:
    def __init__(self, policy: WorkspacePathPolicy) -> None:
        self.policy = policy

    def resolve(self, project: str | None = None, working_directory: str | None = None) -> ProjectContext:
        ref = (project or working_directory or "").strip()
        if not ref:
            raise CodingError(
                CodingErrorCode.PROJECT_CONTEXT_REQUIRED,
                "which project? provide a project path or name (no default project is assumed)",
            )

        root = self._locate(ref)
        if root is None or not root.is_dir():
            raise CodingError(
                CodingErrorCode.PROJECT_NOT_FOUND,
                f"project not found: {ref!r}",
                detail={"ref": ref},
            )
        return self._context_for(root)

    def _locate(self, ref: str) -> Path | None:
        # A workspace-relative path (nested dirs allowed) - validated by policy.
        try:
            candidate = self.policy.resolve(ref)
            return candidate
        except Exception:  # noqa: BLE001 - FileToolError from the policy layer
            return None

    def _context_for(self, root: Path) -> ProjectContext:
        language, framework = self._detect(root)
        git_root = self._find_git_root(root)
        return ProjectContext(
            project_id=hashlib.sha1(str(root).encode("utf-8")).hexdigest()[:12],
            project_root=str(root),
            project_name=root.name,
            language=language,
            framework=framework,
            git_root=git_root,
        )

    @staticmethod
    def _detect(root: Path) -> tuple[str, str]:
        try:
            names = {p.name for p in root.iterdir()}
        except OSError:
            return "", ""
        for marker, language, framework in _MARKERS:
            if any(name.endswith(marker) or name == marker for name in names):
                return language, framework
        return "", ""

    @staticmethod
    def _find_git_root(root: Path) -> str:
        current = root
        while current is not None and str(current) != current.anchor:
            if (current / ".git").exists():
                return str(current)
            parent = current.parent
            current = parent if parent != current else None
        return ""
