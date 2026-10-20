import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from chris.events import utcnow
from chris.personality.models import (
    Personality,
    PersonalityChangeRecord,
    default_personality,
)
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


class TestPersonalityModels(unittest.TestCase):
    def test_default_personality(self):
        personality = default_personality()
        self.assertEqual(personality.core.name, "Chris")
        self.assertEqual(personality.core.proactiveness, "low")
        self.assertEqual(personality.core.language_style.language, "中文（简体）")
        self.assertIsNotNone(personality.core.language_style.formality)

    def test_baseline_sections_present(self):
        personality = default_personality()
        self.assertTrue(personality.core.identity)
        self.assertGreater(len(personality.core.core_traits), 0)
        self.assertGreater(len(personality.core.values), 0)
        self.assertGreater(len(personality.core.boundaries), 0)
        self.assertEqual(personality.communication_style.humor, 0.7)
        self.assertEqual(personality.relationship.relationship_type, "长期陪伴者")
        self.assertFalse(personality.evolution_policy.core_personality_mutable)
        self.assertTrue(personality.evolution_policy.adaptive_personality_mutable)

    def test_new_sections_round_trip(self):
        personality = default_personality()
        personality.relationship.trust = 0.9
        personality.behavioral_preferences.usually.append("主动指出错误")
        restored = Personality.from_dict(personality.to_dict())
        self.assertEqual(restored, personality)
        self.assertEqual(restored.relationship.trust, 0.9)
        self.assertIn("主动指出错误", restored.behavioral_preferences.usually)

    def test_round_trip(self):
        personality = default_personality()
        personality.core.formed_habits.append("greets every morning")
        restored = Personality.from_dict(personality.to_dict())
        self.assertEqual(restored, personality)
        self.assertEqual(restored.core.formed_habits, ["greets every morning"])

    def test_round_trip_with_change_log(self):
        personality = default_personality()
        record = PersonalityChangeRecord(
            timestamp=utcnow(),
            reason="test",
            evidence="evidence",
            before={},
            after={"a": 1},
            confidence=0.5,
            field_name="preference_x",
        )
        personality.change_log.append(record)
        restored = Personality.from_dict(personality.to_dict())
        self.assertEqual(len(restored.change_log), 1)
        self.assertEqual(restored.change_log[0].field_name, "preference_x")
        self.assertEqual(restored.change_log[0].confidence, 0.5)


class TestVersionedPersonalityStore(unittest.TestCase):
    def test_load_returns_default_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = VersionedPersonalityStore(Path(tmp) / "p.json")
            personality = store.current()
            self.assertEqual(personality.core.name, "Chris")
            self.assertEqual(personality.version, 1)

    def test_save_load_round_trip_preserves_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.json"
            store = VersionedPersonalityStore(path)
            store.propose(make_record("humor_preference", "more", 0))
            reloaded = VersionedPersonalityStore(path)
            self.assertEqual(reloaded.pending_evidence(), {"humor_preference": 1})
            self.assertEqual(reloaded.current().version, 1)

    def test_migration_from_v1(self):
        v1 = {
            "version": 1,
            "core": default_personality().core.to_dict(),
            "adaptive": default_personality().adaptive.to_dict(),
            "change_log": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.json"
            path.write_text(
                __import__("json").dumps(v1, ensure_ascii=False), encoding="utf-8"
            )
            store = VersionedPersonalityStore(path)
            self.assertEqual(store.current().version, 1)
            self.assertEqual(store.current().core.name, "Chris")

    def test_baseline_sections_persist(self):
        import json

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.json"
            default = default_personality()
            default.relationship.trust = 0.88
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
            reloaded = VersionedPersonalityStore(path)
            self.assertEqual(reloaded.current().relationship.trust, 0.88)
            self.assertEqual(reloaded.current().evolution_policy.minimum_evidence, 3)
            self.assertEqual(reloaded.current().communication_style.humor, 0.7)


if __name__ == "__main__":
    unittest.main()
