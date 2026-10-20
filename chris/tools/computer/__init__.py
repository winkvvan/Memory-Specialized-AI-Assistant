"""Computer Action Core for Chris (Phase 8.5).

A safe, cancellable, auditable, permission-gated desktop-action layer. Chris
orchestrates through the Tool Core; the ComputerBackend is the only place that
touches the OS desktop. Phase 8.5 performs ACTIONS at fixed coordinates - it
does NOT understand screens (that is Phase 9 Vision), and it does NOT implement
WeChat/application-specific automation or shell execution.
"""

from chris.tools.computer.errors import ComputerError, ComputerErrorCode
from chris.tools.computer.models import (
    ComputerAction,
    ComputerActionResult,
    ComputerStatus,
)

__all__ = [
    "ComputerAction",
    "ComputerActionResult",
    "ComputerError",
    "ComputerErrorCode",
    "ComputerStatus",
]
