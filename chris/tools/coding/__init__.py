"""Coding agent subsystem for Chris (Phase 8.4).

Chris orchestrates external Coding Agents (OpenCode) through a decoupled
adapter. Chris never copies, forks, or reimplements the coding agent - it only
creates CodingTasks, hands them to a controlled process adapter inside a
project workspace, and reports structured results back. Vendor-internal state
(session ids, command schemas) never becomes the Chris internal protocol.
"""

from chris.tools.coding.errors import CodingError, CodingErrorCode
from chris.tools.coding.models import (
    CodingResult,
    CodingStatus,
    CodingTask,
    CodingTaskIntent,
    ProjectContext,
)

__all__ = [
    "CodingError",
    "CodingErrorCode",
    "CodingResult",
    "CodingStatus",
    "CodingTask",
    "CodingTaskIntent",
    "ProjectContext",
]
