"""Behavior Engine - decides whether and how Chris should act.

Phase 1 uses simple deterministic rules only. Output actions are limited to
NO_ACTION / RESPOND / SUGGEST / REMIND. No ML, no randomness.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any


class BehaviorAction(str, Enum):
    NO_ACTION = "NO_ACTION"
    RESPOND = "RESPOND"
    SUGGEST = "SUGGEST"
    REMIND = "REMIND"
    INITIATE = "INITIATE"


@dataclass
class BehaviorDecision:
    action: BehaviorAction
    reason: str = ""
    confidence: float = 1.0


class BehaviorEngine(ABC):
    @abstractmethod
    def decide(self, context: dict[str, Any]) -> BehaviorDecision: ...


class RuleBasedBehaviorEngine(BehaviorEngine):
    """Simple rule engine for phase 1."""

    def decide(self, context: dict[str, Any]) -> BehaviorDecision:
        if context.get("has_user_message"):
            return BehaviorDecision(BehaviorAction.RESPOND, "user is speaking to Chris")
        idle_hours = float(context.get("idle_hours", 0.0))
        if context.get("proactive_enabled") and idle_hours >= 24:
            return BehaviorDecision(
                BehaviorAction.REMIND, "long idle time, worth a gentle reminder"
            )
        return BehaviorDecision(BehaviorAction.NO_ACTION, "nothing to do")
