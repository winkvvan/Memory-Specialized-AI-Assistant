"""Personality Evolution Validator - proposal lifecycle and safety rules.

Maps to phase-3 spec items A-J:
  A single positive feedback must not change personality
  B repeated positive feedback can form a change candidate
  C Core Personality can never be modified
  D oversized changes are rejected
  E approved changes create a new version
  F new versions can be rolled back
  G history survives rollback (append-only) + rollback event + ROLLED_BACK status
  H provider swap does not alter personality (covered in integration tests)
  I restart keeps version and evolution history consistent
  J one anomalous feedback never causes a personality jump
"""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from chris.personality.models import (
    PersonalityChangeRecord,
    ProposalStatus,
    RollbackEvent,
    default_personality,
)
from chris.personality.store import VersionedPersonalityStore

BASE = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


def record(field, value, days, confidence=0.9, old_value=None):
    return PersonalityChangeRecord(
        timestamp=BASE + timedelta(days=days),
        reason="observation",
        evidence="observation",
        before={},
        after={"value": value},
        confidence=confidence,
        field_name=field,
        trait=field,
        old_value=old_value,
        proposed_value=value,
        source="user",
    )


def evolve(store, field, values_and_days):
    for days, value in values_and_days:
        store.propose(record(field, value, days))
    return store.run_evolution()


class TestProposalObject(unittest.TestCase):
    def test_statuses(self):
        self.assertEqual(
            [s.value for s in ProposalStatus],
            ["PENDING", "APPROVED", "REJECTED", "ROLLED_BACK"],
        )

    def test_required_fields(self):
        rec = PersonalityChangeRecord(
            timestamp=BASE,
            reason="r",
            evidence="e",
            before={},
            after={"value": "x"},
            confidence=0.8,
            field_name="humor_preference",
            trait="humor_preference",
            old_value=0.5,
            proposed_value=0.55,
            source="user",
        )
        self.assertTrue(rec.proposal_id)
        self.assertEqual(rec.trait, "humor_preference")
        self.assertEqual(rec.old_value, 0.5)
        self.assertEqual(rec.proposed_value, 0.55)
        self.assertEqual(rec.source, "user")
        self.assertEqual(rec.status, ProposalStatus.PENDING)

    def test_round_trip_preserves_status(self):
        rec = record("humor_preference", "0.55", 0)
        rec.status = ProposalStatus.APPROVED
        rec.decision_reason = "ok"
        restored = PersonalityChangeRecord.from_dict(rec.to_dict())
        self.assertEqual(restored.proposal_id, rec.proposal_id)
        self.assertEqual(restored.status, ProposalStatus.APPROVED)
        self.assertEqual(restored.trait, "humor_preference")
        self.assertEqual(restored.proposed_value, "0.55")


class TestSpecAtoJ(unittest.TestCase):
    def _store(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return VersionedPersonalityStore(Path(tmp.name) / "p.json")

    def test_a_single_positive_feedback_does_not_change_personality(self):
        store = self._store()
        before = dict(store.current().adaptive.preferences)
        store.propose(record("humor_preference", "0.55", 0))
        results = store.run_evolution()
        self.assertFalse(results[0].approved)
        self.assertEqual(store.current().version, 1)
        self.assertEqual(store.current().adaptive.preferences, before)

    def test_b_repeated_positive_feedback_forms_candidate(self):
        store = self._store()
        results = evolve(store, "humor_preference", [(0, "0.55"), (5, "0.55"), (10, "0.55")])
        self.assertTrue(results[0].approved)
        self.assertEqual(store.current().version, 2)
        self.assertEqual(
            store.current().adaptive.preferences["humor_preference"], {"value": "0.55"}
        )

    def test_c_core_never_modified(self):
        store = self._store()
        before = store.current().core.to_dict()
        for field in ("identity", "core_traits", "values", "boundaries", "base_character"):
            results = evolve(store, field, [(0, "x"), (5, "x"), (10, "x")])
            self.assertFalse(results[0].approved, field)
        self.assertEqual(store.current().core.to_dict(), before)
        self.assertEqual(store.current().version, 1)

    def test_d_oversized_change_rejected(self):
        store = self._store()
        results = evolve(store, "humor_preference", [(0, "0.9"), (5, "0.9"), (10, "0.9")])
        self.assertFalse(results[0].approved)
        self.assertIn("too large", results[0].reason)
        self.assertEqual(store.current().version, 1)

    def test_d_small_numeric_change_approved(self):
        store = self._store()
        results = evolve(store, "humor_preference", [(0, "0.55"), (5, "0.55"), (10, "0.55")])
        self.assertTrue(results[0].approved)

    def test_e_approval_creates_new_version(self):
        store = self._store()
        evolve(store, "humor_preference", [(0, "0.55"), (5, "0.55"), (10, "0.55")])
        self.assertEqual(store.current().version, 2)
        self.assertEqual(len(store.history()), 2)
        self.assertEqual(store.history()[-1].change.status, ProposalStatus.APPROVED)

    def test_f_new_version_can_rollback(self):
        store = self._store()
        evolve(store, "humor_preference", [(0, "0.55"), (5, "0.55"), (10, "0.55")])
        self.assertEqual(store.current().version, 2)
        self.assertTrue(store.rollback(1))
        self.assertEqual(store.current().version, 1)

    def test_g_rollback_keeps_history_and_records_event(self):
        store = self._store()
        evolve(store, "humor_preference", [(0, "0.55"), (5, "0.55"), (10, "0.55")])
        store.rollback(1)
        self.assertEqual(len(store.history()), 2, "history must be append-only")
        events = store.rollback_events()
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0].from_version, events[0].to_version), (2, 1))
        abandoned = store.history()[1].change
        self.assertEqual(abandoned.status, ProposalStatus.ROLLED_BACK)
        log = store.proposal_log()
        self.assertTrue(log)
        self.assertEqual(log[0].status, ProposalStatus.ROLLED_BACK)

    def test_i_restart_keeps_version_and_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.json"
            store = VersionedPersonalityStore(path)
            evolve(store, "humor_preference", [(0, "0.55"), (5, "0.55"), (10, "0.55")])
            store.rollback(1)
            reloaded = VersionedPersonalityStore(path)
            self.assertEqual(reloaded.current().version, 1)
            self.assertEqual(len(reloaded.history()), 2)
            self.assertEqual(len(reloaded.rollback_events()), 1)
            self.assertEqual(len(reloaded.proposal_log()), 1)
            self.assertEqual(reloaded.proposal_log()[0].status, ProposalStatus.ROLLED_BACK)

    def test_j_single_anomalous_feedback_does_not_jump(self):
        store = self._store()
        before = dict(store.current().adaptive.preferences)
        results = evolve(store, "humor_preference", [(0, "0.2"), (5, "0.55"), (10, "0.55")])
        self.assertFalse(results[0].approved)
        self.assertIn("conflicting", results[0].reason)
        self.assertEqual(store.current().version, 1)
        self.assertEqual(store.current().adaptive.preferences, before)

    def test_rejected_proposals_are_traceable(self):
        store = self._store()
        store.propose(record("humor_preference", "0.9", 0))
        store.propose(record("humor_preference", "0.9", 5))
        store.propose(record("humor_preference", "0.9", 10))
        store.run_evolution()
        log = store.proposal_log()
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0].status, ProposalStatus.REJECTED)
        self.assertIn("too large", log[0].decision_reason)


if __name__ == "__main__":
    unittest.main()
