"""Coding task data models.

OpenCode's internal state never becomes the Chris protocol - these are the
Chris-side, model-independent structures used for scheduling, tracking and
reporting coding work.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from chris.events import utcnow


class CodingStatus(str, Enum):
    PENDING = "PENDING"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMEOUT = "TIMEOUT"
    DENIED = "DENIED"


class CodingTaskIntent(str, Enum):
    NOT_CODING = "NOT_CODING"
    CODING = "CODING"


@dataclass
class ProjectContext:
    """Minimal project identification derived from project_root.

    Not a full project analyzer: language/framework are inferred from marker
    files only; never scans the whole disk.
    """

    project_id: str
    project_root: str
    project_name: str
    language: str = ""
    framework: str = ""
    git_root: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "project_root": self.project_root,
            "project_name": self.project_name,
            "language": self.language,
            "framework": self.framework,
            "git_root": self.git_root,
        }


@dataclass
class CodingTask:
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    project_root: str = ""
    project_name: str = ""
    prompt: str = ""
    status: CodingStatus = CodingStatus.PENDING
    created_at: datetime = field(default_factory=utcnow)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    provider: str = ""
    session_id: str = ""
    exit_code: int | None = None
    summary: str = ""
    changed_files: list[str] = field(default_factory=list)
    test_status: str = "NOT_RUN"
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        iso = lambda d: d.isoformat() if d else None
        return {
            "task_id": self.task_id,
            "project_root": self.project_root,
            "project_name": self.project_name,
            "prompt": self.prompt[:300],
            "status": self.status.value,
            "created_at": iso(self.created_at),
            "started_at": iso(self.started_at),
            "completed_at": iso(self.completed_at),
            "provider": self.provider,
            "session_id": self.session_id,
            "exit_code": self.exit_code,
            "summary": self.summary,
            "changed_files": list(self.changed_files),
            "test_status": self.test_status,
            "error": self.error,
            "metadata": dict(self.metadata),
        }


@dataclass
class CodingResult:
    """Structured outcome fed back to Chris / the LLM.

    Never the raw OpenCode stdout/stderr dump - a curated projection plus a
    bounded raw tail for debugging.
    """

    status: CodingStatus
    summary: str = ""
    changed_files: list[str] = field(default_factory=list)
    tests: str = "NOT_RUN"
    exit_code: int | None = None
    duration_seconds: float = 0.0
    error: str | None = None
    raw_tail: str = ""

    def to_text(self) -> str:
        """LLM-friendly projection (never the full raw output)."""
        lines = [f"Coding task {self.status.value.lower()}."]
        if self.summary:
            lines.append(f"Summary: {self.summary}")
        if self.changed_files:
            lines.append("Changed files:")
            for path in self.changed_files:
                lines.append(f"- {path}")
        lines.append(f"Tests: {self.tests}")
        if self.exit_code is not None:
            lines.append(f"Exit code: {self.exit_code}")
        if self.error:
            lines.append(f"Reason: {self.error}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "summary": self.summary,
            "changed_files": list(self.changed_files),
            "tests": self.tests,
            "exit_code": self.exit_code,
            "duration_seconds": self.duration_seconds,
            "error": self.error,
        }
