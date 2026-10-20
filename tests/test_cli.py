import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

MOCK_CONFIG = {
    "llm": {"provider": "mock", "model": "mock-default", "options": {}},
    "personality_file": "data/personality.json",
    "memory_file": "data/memory.json",
    "world_state_file": "data/world.json",
    "temporal_file": "data/temporal.json",
    "log_file": "data/chris.log",
    "log_level": "INFO",
    "max_response_tokens": 512,
    "time_threshold_hours": [1, 8, 24],
}


class TestCLI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.config_path = Path(cls.tmp.name) / "config.json"
        cls.config_path.write_text(
            json.dumps(MOCK_CONFIG, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _env(self):
        return {**os.environ, "CHRIS_CONFIG": str(self.config_path)}

    def test_health_flag_runs_and_exits(self):
        result = subprocess.run(
            [sys.executable, "-m", "chris", "--health"],
            cwd=PROJECT_ROOT,
            env=self._env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("result=ok", result.stdout)
        self.assertIn("provider=mock", result.stdout)

    def test_health_command_in_session(self):
        result = subprocess.run(
            [sys.executable, "-m", "chris"],
            cwd=PROJECT_ROOT,
            env=self._env(),
            input="/health\nquit\n",
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("result=ok", result.stdout)


if __name__ == "__main__":
    unittest.main()
