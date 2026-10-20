import tempfile
import unittest
from pathlib import Path

from chris.bootstrap import ensure_default_state_files
from chris.config import Config
from chris.memory.ecology import MemoryEcology
from chris.personality.store import VersionedPersonalityStore


class TestDefaultStateBootstrap(unittest.TestCase):
    def _config(self, data_root: Path) -> Config:
        cfg = Config()
        cfg.personality_file = str(data_root / "personality.json")
        cfg.memory_file = str(data_root / "chats" / "memory.json")
        return cfg

    def test_first_run_creates_generic_editable_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp) / "data"
            cfg = self._config(data_root)

            created = ensure_default_state_files(cfg, data_root=data_root)

            self.assertEqual(len(created), 3)
            store = VersionedPersonalityStore(Path(cfg.personality_file))
            self.assertEqual(store.current().core.name, "Assistant")
            memory = MemoryEcology(Path(cfg.memory_file))
            self.assertEqual(memory.list_active(limit=10), [])

    def test_existing_files_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp) / "data"
            cfg = self._config(data_root)
            personality = Path(cfg.personality_file)
            personality.parent.mkdir(parents=True)
            personality.write_text('{"core": {"name": "Custom"}}', encoding="utf-8")

            ensure_default_state_files(cfg, data_root=data_root)

            self.assertEqual(personality.read_text(encoding="utf-8"), '{"core": {"name": "Custom"}}')

    def test_paths_outside_data_root_are_left_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp) / "data"
            outside = Path(tmp) / "outside"
            cfg = self._config(outside)

            created = ensure_default_state_files(cfg, data_root=data_root)

            self.assertEqual(created, [])
            self.assertFalse((outside / "personality.json").exists())
            self.assertFalse((outside / "chats" / "memory.json").exists())


if __name__ == "__main__":
    unittest.main()
