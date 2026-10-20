"""Live tests against the real DeepSeek API.

These run only when a DeepSeek API key is present in the environment. Without a
key they are skipped, keeping the default test run offline.
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

from chris.llm.openai_compat import DeepSeekProvider

_HAS_KEY = bool(os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("CHRIS_API_KEY"))
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


@unittest.skipUnless(_HAS_KEY, "DEEPSEEK_API_KEY not set; skipping live API test")
class TestRealModelLive(unittest.TestCase):
    def test_health_check(self):
        provider = DeepSeekProvider()
        self.assertTrue(provider.health_check())

    def test_generate_returns_nonempty(self):
        provider = DeepSeekProvider()
        reply = provider.generate(
            [{"role": "user", "content": "请用一句话回复：你好"}], max_tokens=128
        )
        self.assertTrue(reply.strip())
        self.assertIn("你好", reply)

    def test_cli_conversation_end_to_end(self):
        env = {**os.environ, "PYTHONUTF8": "1"}
        result = subprocess.run(
            [sys.executable, "-m", "chris"],
            cwd=PROJECT_ROOT,
            env=env,
            input="你好，请说一句自我介绍\nquit\n",
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Chris>", result.stdout)


if __name__ == "__main__":
    unittest.main()
