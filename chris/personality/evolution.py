"""Personality Evolution safety layer.

A single observation is never enough to change personality. Signals accumulate
as evidence for a candidate field; a change is approved only when the evidence
is sufficient, consistent, confident and spans a minimum time window. Approved
changes land in the adaptive layer and produce a new personality version.
Core personality is never modified by evolution.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from chris.personality.models import (
    CorePersonality,
    EvolutionPolicy,
    PersonalityChangeRecord,
)


@dataclass
class EvolutionConfig:
    min_evidence: int = 3
    min_confidence: float = 0.7
    min_window_days: int = 7
    max_change_delta: float = 0.2


def config_from_policy(policy: EvolutionPolicy) -> EvolutionConfig:
    """Map data-driven evolution policy onto validator configuration."""
    cfg = EvolutionConfig()
    cfg.min_evidence = max(policy.minimum_evidence, 1)
    if not policy.allow_single_event_personality_change:
        cfg.min_evidence = max(cfg.min_evidence, 2)
    cfg.max_change_delta = policy.max_change_delta
    return cfg


@dataclass
class ValidationResult:
    field_name: str
    approved: bool
    reason: str


def _core_field_names() -> set[str]:
    return set(CorePersonality.__dataclass_fields__)


class EvolutionValidator:
    """Deterministic rule-based validator. No ML, no randomness."""

    def __init__(self, config: EvolutionConfig | None = None) -> None:
        self.config = config or EvolutionConfig()

    def validate(
        self,
        trait: str,
        observations: list[PersonalityChangeRecord],
        core: CorePersonality,
        old_value: Any = None,
        proposed_value: Any = None,
    ) -> ValidationResult:
        if trait in _core_field_names():
            return ValidationResult(trait, False, f"core field {trait!r} is immutable")
        if not observations:
            return ValidationResult(trait, False, "no evidence")
        if len(observations) < self.config.min_evidence:
            return ValidationResult(
                trait,
                False,
                f"need at least {self.config.min_evidence} evidence entries, got {len(observations)}",
            )
        target = observations[0].after
        if not all(entry.after == target for entry in observations):
            return ValidationResult(
                trait, False, "conflicting evidence: proposed values differ"
            )
        average = sum(entry.confidence for entry in observations) / len(observations)
        if average < self.config.min_confidence:
            return ValidationResult(
                trait,
                False,
                f"average confidence {average:.2f} below threshold {self.config.min_confidence}",
            )
        first = min(entry.timestamp for entry in observations)
        last = max(entry.timestamp for entry in observations)
        span_days = (last - first).total_seconds() / 86400.0
        if span_days < self.config.min_window_days:
            return ValidationResult(
                trait,
                False,
                f"evidence spans {span_days:.1f} days, need at least {self.config.min_window_days}",
            )
        if not self._magnitude_ok(old_value, proposed_value):
            return ValidationResult(
                trait, False, self._magnitude_reason(old_value, proposed_value)
            )
        return ValidationResult(trait, True, "evidence sufficient and consistent")

    def _magnitude_ok(self, old_value: Any, proposed_value: Any) -> bool:
        try:
            old = float(old_value)
            proposed = float(proposed_value)
        except (TypeError, ValueError):
            return True
        return abs(proposed - old) <= self.config.max_change_delta

    def _magnitude_reason(self, old_value: Any, proposed_value: Any) -> str:
        try:
            delta = abs(float(proposed_value) - float(old_value))
            return (
                f"proposed change too large: delta {delta:.2f} exceeds "
                f"allowed max {self.config.max_change_delta:.2f}"
            )
        except (TypeError, ValueError):
            return "proposed change too large"
