import tempfile
import unittest
from pathlib import Path

from chris.config import Config, LLMConfig


class TestConfig(unittest.TestCase):
    def test_defaults(self):
        config = Config()
        self.assertEqual(config.llm.provider, "mock")
        self.assertEqual(config.time_threshold_hours, (1, 8, 24))

    def test_load_returns_default_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Config.load(Path(tmp) / "nope.json")
            self.assertEqual(config.llm.provider, "mock")

    def test_save_load_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            config = Config(llm=LLMConfig(provider="mock", model="m", options={"a": 1}))
            config.time_threshold_hours = (1, 2)
            config.save(path)
            restored = Config.load(path)
            self.assertEqual(restored.llm.provider, "mock")
            self.assertEqual(restored.llm.model, "m")
            self.assertEqual(restored.llm.options, {"a": 1})
            self.assertEqual(restored.time_threshold_hours, (1, 2))

    def test_resolve_relative_to_project_root(self):
        config = Config()
        resolved = config.resolve("data/personality.json")
        self.assertTrue(resolved.is_absolute())
        self.assertEqual(resolved.name, "personality.json")


if __name__ == "__main__":
    unittest.main()
