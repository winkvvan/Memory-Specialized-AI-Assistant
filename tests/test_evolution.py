import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from chris.personality.evolution import EvolutionConfig, EvolutionValidator
from chris.personality.models import CorePersonality, PersonalityChangeRecord
from chris.personality.store import VersionedPersonalityStore


def make_record(field, value, offset_days, confidence=0.9):
    base = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    return PersonalityChangeRecord(
        timestamp=base + timedelta(days=offset_days),
        reason="observation",
        evidence="observation",
        before={},
        after={"value": value},
        confidence=confidence,
        field_name=field,
    )


class TestEvolutionValidator(unittest.TestCase):
    def setUp(self):
        self.validator = EvolutionValidator()
        self.core = CorePersonality()

    def test_insufficient_evidence(self):
        evidence = [make_record("humor_preference", "more", 0), make_record("humor_preference", "more", 8)]
        result = self.validator.validate("humor_preference", evidence, self.core)
        self.assertFalse(result.approved)
        self.assertIn("need at least 3", result.reason)

    def test_conflicting_evidence(self):
        evidence = [
            make_record("humor_preference", "more", 0),
            make_record("humor_preference", "less", 5),
            make_record("humor_preference", "more", 10),
        ]
        result = self.validator.validate("humor_preference", evidence, self.core)
        self.assertFalse(result.approved)
        self.assertIn("conflicting", result.reason)

    def test_low_confidence(self):
        evidence = [
            make_record("humor_preference", "more", 0, confidence=0.5),
            make_record("humor_preference", "more", 5, confidence=0.5),
            make_record("humor_preference", "more", 10, confidence=0.5),
        ]
        result = self.validator.validate("humor_preference", evidence, self.core)
        self.assertFalse(result.approved)
        self.assertIn("confidence", result.reason)

    def test_short_window(self):
        evidence = [
            make_record("humor_preference", "more", 0),
            make_record("humor_preference", "more", 1),
            make_record("humor_preference", "more", 2),
        ]
        result = self.validator.validate("humor_preference", evidence, self.core)
        self.assertFalse(result.approved)
        self.assertIn("spans", result.reason)

    def test_core_field_rejected(self):
        evidence = [
            make_record("base_character", "a", 0),
            make_record("base_character", "a", 5),
            make_record("base_character", "a", 10),
        ]
        result = self.validator.validate("base_character", evidence, self.core)
        self.assertFalse(result.approved)
        self.assertIn("immutable", result.reason)

    def test_new_core_fields_rejected(self):
        for field in ("identity", "core_traits", "values", "boundaries"):
            with self.subTest(field=field):
                evidence = [
                    make_record(field, "x", 0),
                    make_record(field, "x", 5),
                    make_record(field, "x", 10),
                ]
                result = self.validator.validate(field, evidence, self.core)
                self.assertFalse(result.approved)
                self.assertIn("immutable", result.reason)

    def test_policy_maps_onto_config(self):
        from chris.personality.evolution import config_from_policy
        from chris.personality.models import EvolutionPolicy

        policy = EvolutionPolicy(minimum_evidence=5)
        cfg = config_from_policy(policy)
        self.assertEqual(cfg.min_evidence, 5)

    def test_policy_forbids_single_event_change(self):
        from chris.personality.evolution import config_from_policy
        from chris.personality.models import EvolutionPolicy

        policy = EvolutionPolicy(minimum_evidence=1, allow_single_event_personality_change=False)
        cfg = config_from_policy(policy)
        self.assertGreaterEqual(cfg.min_evidence, 2)

    def test_approval_requires_all_rules(self):
        evidence = [
            make_record("humor_preference", "more", 0),
            make_record("humor_preference", "more", 5),
            make_record("humor_preference", "more", 10),
        ]
        result = self.validator.validate("humor_preference", evidence, self.core)
        self.assertTrue(result.approved)


class TestStoreEvolution(unittest.TestCase):
    def _store(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return VersionedPersonalityStore(Path(tmp.name) / "p.json")

    def test_evolve_creates_new_version(self):
        store = self._store()
        store.propose(make_record("humor_preference", "more", 0))
        store.propose(make_record("humor_preference", "more", 5))
        store.propose(make_record("humor_preference", "more", 10))
        results = store.run_evolution()
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].approved)
        self.assertEqual(store.current().version, 2)
        self.assertEqual(
            store.current().adaptive.preferences["humor_preference"], {"value": "more"}
        )

    def test_evolve_keeps_pending_fields_and_rejects_insufficient(self):
        store = self._store()
        store.propose(make_record("greeting_preference", "warm", 0))
        results = store.run_evolution()
        self.assertFalse(results[0].approved)
        self.assertEqual(store.current().version, 1)
        self.assertEqual(store.pending_evidence(), {})

    def test_core_never_modified_by_evolution(self):
        store = self._store()
        before = store.current().core.to_dict()
        store.propose(make_record("base_character", "hacked", 0))
        store.propose(make_record("base_character", "hacked", 5))
        store.propose(make_record("base_character", "hacked", 10))
        store.run_evolution()
        self.assertEqual(store.current().core.to_dict(), before)
        self.assertEqual(store.current().version, 1)

    def test_rollback_restores_earlier_snapshot(self):
        store = self._store()
        store.propose(make_record("greeting_preference", "warm", 0))
        store.propose(make_record("greeting_preference", "warm", 5))
        store.propose(make_record("greeting_preference", "warm", 10))
        store.run_evolution()
        self.assertEqual(store.current().version, 2)
        self.assertTrue(store.rollback(1))
        self.assertEqual(store.current().version, 1)
        self.assertNotIn("greeting_preference", store.current().adaptive.preferences)

    def test_rollback_unknown_version(self):
        store = self._store()
        self.assertFalse(store.rollback(99))

    def test_persistence_of_history_and_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.json"
            store = VersionedPersonalityStore(path)
            store.propose(make_record("humor_preference", "more", 0))
            store.propose(make_record("humor_preference", "more", 5))
            store.propose(make_record("humor_preference", "more", 10))
            store.run_evolution()
            reloaded = VersionedPersonalityStore(path)
            self.assertEqual(reloaded.current().version, 2)
            self.assertEqual(
                reloaded.current().adaptive.preferences["humor_preference"],
                {"value": "more"},
            )
            self.assertEqual(len(reloaded.history()), 2)

    def test_evolution_policy_from_file_drives_validator(self):
        import json

        from chris.personality.models import default_personality

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.json"
            default = default_personality()
            default.evolution_policy.minimum_evidence = 4
            data = {
                "core": default.core.to_dict(),
                "communication_style": default.communication_style.to_dict(),
                "relationship": default.relationship.to_dict(),
                "behavioral_preferences": default.behavioral_preferences.to_dict(),
                "evolution_policy": default.evolution_policy.to_dict(),
                "current_version": 1,
                "history": [
                    {
                        "version": 1,
                        "adaptive": default.adaptive.to_dict(),
                        "created_at": "2026-08-01T00:00:00+00:00",
                        "change": None,
                    }
                ],
                "pending": {},
            }
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            store = VersionedPersonalityStore(path)
            for days in (0, 3, 8):
                store.propose(make_record("humor_preference", "more", days))
            results = store.run_evolution()
            self.assertFalse(results[0].approved)
            self.assertIn("need at least 4", results[0].reason)
            for days in (0, 3, 8, 10):
                store.propose(make_record("humor_preference", "more", days))
            results = store.run_evolution()
            self.assertTrue(results[0].approved)


if __name__ == "__main__":
    unittest.main()
