"""Permission Confirmation subsystem (Phase 8.6).

A ConfirmationRequest records an intercepted high-risk ToolCall and awaits an
explicit user decision. Confirmation is one-shot, TTL-bounded and in-memory
(pending requests do not survive a restart). The ConfirmationManager never
executes anything - the ToolExecutor drives approve-and-execute.
"""

from chris.tools.confirmation.errors import ConfirmationError, ConfirmationErrorCode
from chris.tools.confirmation.models import ConfirmationRequest, ConfirmationStatus
from chris.tools.confirmation.manager import ConfirmationManager

__all__ = [
    "ConfirmationError",
    "ConfirmationErrorCode",
    "ConfirmationManager",
    "ConfirmationRequest",
    "ConfirmationStatus",
]
