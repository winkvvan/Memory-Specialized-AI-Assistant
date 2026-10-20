"""Versioned persistence and guarded changes for personality.

Personality v1 -> v2 -> v3 ... : every approved evolution appends a new version
snapshot. History is append-only; rollback only moves the current-version
pointer and records a RollbackEvent (nothing is deleted). Core personality is
stored once and shared across versions; evolution only ever writes to the
adaptive layer.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from chris.events import utcnow
from chris.personality.evolution import (
    EvolutionConfig,
    EvolutionValidator,
    ValidationResult,
    config_from_policy,
)
from chris.personality.models import (
    AdaptivePersonality,
    BehavioralPreferences,
    CommunicationStyle,
    CorePersonality,
    EvolutionPolicy,
    Personality,
    PersonalityChangeRecord,
    PersonalityVersion,
    ProposalStatus,
    Relationship,
    RollbackEvent,
    default_personality,
)


class VersionedPersonalityStore:
    def __init__(
        self, path: Path, evolution_config: EvolutionConfig | None = None
    ) -> None:
        self.path = path
        self._core = CorePersonality()
        self._history: list[PersonalityVersion] = []
        self._pending: dict[str, list[PersonalityChangeRecord]] = {}
        self._proposal_log: list[PersonalityChangeRecord] = []
        self._rollback_log: list[RollbackEvent] = []
        self._current_version = 1
        self._communication_style = CommunicationStyle()
        self._relationship = Relationship()
        self._behavioral_preferences = BehavioralPreferences()
        self._evolution_policy = EvolutionPolicy()
        self._load()
        if evolution_config is not None:
            self.validator = EvolutionValidator(evolution_config)
        else:
            self.validator = EvolutionValidator(config_from_policy(self._evolution_policy))

    def current(self) -> Personality:
        active = self._active_version()
        if active is None:
            return default_personality()
        change_log = []
        for version in self._history:
            if version.version > self._current_version:
                break
            if version.change:
                change_log.append(version.change)
        return Personality(
            core=self._core,
            adaptive=active.adaptive,
            communication_style=self._communication_style,
            relationship=self._relationship,
            behavioral_preferences=self._behavioral_preferences,
            evolution_policy=self._evolution_policy,
            version=active.version,
            change_log=change_log,
        )

    def history(self) -> list[PersonalityVersion]:
        return list(self._history)

    def proposal_log(self) -> list[PersonalityChangeRecord]:
        return list(self._proposal_log)

    def rollback_events(self) -> list[RollbackEvent]:
        return list(self._rollback_log)

    def pending_evidence(self) -> dict[str, int]:
        return {trait: len(items) for trait, items in self._pending.items()}

    def propose(self, change: PersonalityChangeRecord) -> None:
        trait = change.trait or change.field_name
        change.status = ProposalStatus.PENDING
        self._pending.setdefault(trait, []).append(change)
        self._save()

    def run_evolution(self) -> list[ValidationResult]:
        results: list[ValidationResult] = []
        for trait in list(self._pending):
            observations = self._pending.pop(trait)
            active = self._active_version()
            old_value = active.adaptive.preferences.get(trait) if active else None
            if isinstance(old_value, dict):
                old_value = old_value.get("value")
            proposed_value = observations[0].proposed_value
            result = self.validator.validate(
                trait,
                observations,
                self._core,
                old_value=old_value,
                proposed_value=proposed_value,
            )
            if result.approved:
                self._apply(trait, observations, result)
            else:
                self._log_rejected(trait, observations, result)
            results.append(result)
        self._save()
        return results

    def rollback(self, to_version: int) -> bool:
        if not any(version.version == to_version for version in self._history):
            return False
        if to_version == self._current_version:
            return True
        self._rollback_log.append(
            RollbackEvent(
                timestamp=utcnow(),
                from_version=self._current_version,
                to_version=to_version,
            )
        )
        abandoned_ids = {
            version.change.proposal_id
            for version in self._history
            if version.version > to_version
            and version.change is not None
            and version.change.status == ProposalStatus.APPROVED
        }
        for version in self._history:
            if version.change is not None and version.version > to_version:
                version.change.status = ProposalStatus.ROLLED_BACK
        for record in self._proposal_log:
            if (
                record.status == ProposalStatus.APPROVED
                and record.proposal_id in abandoned_ids
            ):
                record.status = ProposalStatus.ROLLED_BACK
        self._current_version = to_version
        self._save()
        return True

    def _apply(
        self,
        trait: str,
        observations: list[PersonalityChangeRecord],
        result: ValidationResult,
    ) -> None:
        active = self._active_version()
        adaptive = AdaptivePersonality.from_dict(active.adaptive.to_dict())
        raw_before = active.adaptive.preferences.get(trait, {})
        before = raw_before if isinstance(raw_before, dict) else {"value": raw_before}
        after = observations[0].after
        adaptive.preferences[trait] = after
        if trait not in adaptive.recent_adjustments:
            adaptive.recent_adjustments.append(trait)
        adaptive.updated_at = observations[-1].timestamp
        average = sum(entry.confidence for entry in observations) / len(observations)
        first = observations[0]
        change = PersonalityChangeRecord(
            proposal_id=first.proposal_id,
            timestamp=observations[-1].timestamp,
            reason=first.reason,
            evidence=f"{len(observations)} observations over time",
            before=before,
            after=after,
            confidence=average,
            field_name=trait,
            trait=trait,
            old_value=before.get("value") if isinstance(before, dict) else before,
            proposed_value=first.proposed_value,
            source=first.source,
            status=ProposalStatus.APPROVED,
            decision_reason=result.reason,
        )
        new_version = max((v.version for v in self._history), default=0) + 1
        self._history.append(
            PersonalityVersion(
                version=new_version,
                adaptive=adaptive,
                created_at=utcnow(),
                change=change,
            )
        )
        self._current_version = new_version
        self._proposal_log.append(change)

    def _log_rejected(
        self,
        trait: str,
        observations: list[PersonalityChangeRecord],
        result: ValidationResult,
    ) -> None:
        first = observations[0]
        average = sum(entry.confidence for entry in observations) / len(observations)
        record = PersonalityChangeRecord(
            proposal_id=first.proposal_id,
            timestamp=observations[-1].timestamp,
            reason=first.reason,
            evidence=f"{len(observations)} observations",
            before=first.before,
            after=first.after,
            confidence=average,
            field_name=trait,
            trait=trait,
            old_value=None,
            proposed_value=first.proposed_value,
            source=first.source,
            status=ProposalStatus.REJECTED,
            decision_reason=result.reason,
        )
        self._proposal_log.append(record)

    def _active_version(self) -> PersonalityVersion | None:
        if not self._history:
            return None
        for version in self._history:
            if version.version == self._current_version:
                return version
        return self._history[-1]

    def _load(self) -> None:
        if not self.path.exists():
            self._init_default()
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, OSError):
            self._init_default()
            return
        if isinstance(raw, dict) and "history" in raw:
            self._load_v2(raw)
        else:
            self._migrate_from_v1(raw)
        if not self._history:
            self._init_default()

    def _load_v2(self, raw: dict[str, Any]) -> None:
        core_raw = raw.get("core")
        self._core = (
            CorePersonality.from_dict(core_raw) if isinstance(core_raw, dict) else CorePersonality()
        )
        self._communication_style = (
            CommunicationStyle.from_dict(raw["communication_style"])
            if isinstance(raw.get("communication_style"), dict)
            else CommunicationStyle()
        )
        self._relationship = (
            Relationship.from_dict(raw["relationship"])
            if isinstance(raw.get("relationship"), dict)
            else Relationship()
        )
        self._behavioral_preferences = (
            BehavioralPreferences.from_dict(raw["behavioral_preferences"])
            if isinstance(raw.get("behavioral_preferences"), dict)
            else BehavioralPreferences()
        )
        self._evolution_policy = (
            EvolutionPolicy.from_dict(raw["evolution_policy"])
            if isinstance(raw.get("evolution_policy"), dict)
            else EvolutionPolicy()
        )
        history_raw = raw.get("history", [])
        if isinstance(history_raw, list):
            self._history = [
                PersonalityVersion.from_dict(item)
                for item in history_raw
                if isinstance(item, dict)
            ]
        current = raw.get("current_version")
        self._current_version = int(current) if isinstance(current, int) else 1
        if self._history:
            valid = {v.version for v in self._history}
            if self._current_version not in valid:
                self._current_version = self._history[-1].version
        pending_raw = raw.get("pending")
        if isinstance(pending_raw, dict):
            for field, items in pending_raw.items():
                if isinstance(items, list):
                    self._pending[field] = [
                        PersonalityChangeRecord.from_dict(item)
                        for item in items
                        if isinstance(item, dict)
                    ]
        proposal_raw = raw.get("proposals")
        if isinstance(proposal_raw, list):
            self._proposal_log = [
                PersonalityChangeRecord.from_dict(item)
                for item in proposal_raw
                if isinstance(item, dict)
            ]
        rollback_raw = raw.get("rollbacks")
        if isinstance(rollback_raw, list):
            self._rollback_log = [
                RollbackEvent.from_dict(item)
                for item in rollback_raw
                if isinstance(item, dict)
            ]

    def _migrate_from_v1(self, raw: Any) -> None:
        if not isinstance(raw, dict):
            self._init_default()
            return
        core_raw = raw.get("core")
        self._core = (
            CorePersonality.from_dict(core_raw) if isinstance(core_raw, dict) else CorePersonality()
        )
        adaptive_raw = raw.get("adaptive")
        adaptive = (
            AdaptivePersonality.from_dict(adaptive_raw)
            if isinstance(adaptive_raw, dict)
            else AdaptivePersonality()
        )
        version = int(raw.get("version", 1))
        self._history = [
            PersonalityVersion(
                version=version, adaptive=adaptive, created_at=utcnow(), change=None
            )
        ]
        self._current_version = version

    def _init_default(self) -> None:
        default = default_personality()
        self._core = default.core
        self._communication_style = default.communication_style
        self._relationship = default.relationship
        self._behavioral_preferences = default.behavioral_preferences
        self._evolution_policy = default.evolution_policy
        self._history = [
            PersonalityVersion(
                version=1, adaptive=default.adaptive, created_at=utcnow(), change=None
            )
        ]
        self._current_version = 1
        self._pending = {}
        self._proposal_log = []
        self._rollback_log = []

    def save(self) -> None:
        self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "core": self._core.to_dict(),
            "communication_style": self._communication_style.to_dict(),
            "relationship": self._relationship.to_dict(),
            "behavioral_preferences": self._behavioral_preferences.to_dict(),
            "evolution_policy": self._evolution_policy.to_dict(),
            "current_version": self._current_version,
            "history": [version.to_dict() for version in self._history],
            "pending": {
                trait: [record.to_dict() for record in records]
                for trait, records in self._pending.items()
            },
            "proposals": [record.to_dict() for record in self._proposal_log],
            "rollbacks": [event.to_dict() for event in self._rollback_log],
        }
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
