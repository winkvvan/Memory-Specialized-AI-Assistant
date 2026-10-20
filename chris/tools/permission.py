"""Permission layer for tools.

The policy decides whether a tool may run given its declared risk level and the
current context. Phase 8.6 extends the decision to a tri-state: ALLOW, DENY and
CONFIRMATION_REQUIRED. Confirmation handling lives in a separate
``ConfirmationManager`` - this module only classifies; it never waits for or
records user decisions.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from chris.tools.base import ToolContext
from chris.tools.models import RiskLevel, ToolDefinition


class PermissionOutcome(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"


@dataclass(frozen=True)
class PermissionDecision:
    allowed: bool
    reason: str = ""
    outcome: PermissionOutcome = PermissionOutcome.ALLOW
    confirmation_required: bool = False

    @classmethod
    def allow(cls, reason: str = "") -> "PermissionDecision":
        return cls(True, reason, PermissionOutcome.ALLOW, False)

    @classmethod
    def deny(cls, reason: str = "") -> "PermissionDecision":
        return cls(False, reason, PermissionOutcome.DENY, False)

    @classmethod
    def require_confirmation(cls, reason: str = "") -> "PermissionDecision":
        return cls(False, reason, PermissionOutcome.CONFIRMATION_REQUIRED, True)


class PermissionPolicy(ABC):
    @abstractmethod
    def check(
        self, definition: ToolDefinition, context: ToolContext
    ) -> PermissionDecision: ...


class DefaultPermissionPolicy(PermissionPolicy):
    """READ_ONLY / LOW are allowed; MEDIUM / HIGH require confirmation;
    CRITICAL is denied outright (never confirmable)."""

    DEFAULT_ALLOWED = frozenset({RiskLevel.READ_ONLY, RiskLevel.LOW})
    CONFIRMABLE = frozenset({RiskLevel.MEDIUM, RiskLevel.HIGH})

    def __init__(
        self,
        allowed_risks: Iterable[RiskLevel] | None = None,
        allowlist_names: Iterable[str] = (),
    ) -> None:
        self._allowed_risks = frozenset(
            allowed_risks if allowed_risks is not None else self.DEFAULT_ALLOWED
        )
        self._allowlist = frozenset(allowlist_names)

    def check(
        self, definition: ToolDefinition, context: ToolContext
    ) -> PermissionDecision:
        if definition.name in self._allowlist:
            return PermissionDecision.allow("explicitly allow-listed")
        if definition.risk_level in self._allowed_risks:
            return PermissionDecision.allow(
                f"risk level {definition.risk_level.value} allowed"
            )
        if definition.risk_level in self.CONFIRMABLE:
            return PermissionDecision.require_confirmation(
                f"risk level {definition.risk_level.value} requires user confirmation"
            )
        return PermissionDecision.deny(
            f"risk level {definition.risk_level.value} is not allowed"
        )
