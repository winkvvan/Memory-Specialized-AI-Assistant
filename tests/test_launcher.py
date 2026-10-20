import tempfile
import threading
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import launcher as L

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _make_config(tmp: str):
    from chris.config import Config

    config = Config()
    config.personality_file = str(Path(tmp) / "p.json")
    config.memory_file = str(Path(tmp) / "m.json")
    config.temporal_file = str(Path(tmp) / "t.json")
    config.world_state_file = str(Path(tmp) / "w.json")
    return config


class TestChatPipeline(unittest.TestCase):
    JUDGE_NOOP = '{"confirm_memory_ids": [], "want_old_memory": false}'

    def _runtime(self, tmp, replies=("你好，我是 Chris。",)):
        from chris.llm.mock import MockProvider

        return L.build_chat_pipeline(
            _make_config(tmp),
            provider=MockProvider(responses=[self.JUDGE_NOOP, *list(replies)]),
        )

    def test_chat_turn_returns_reply_and_writes_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = self._runtime(tmp)
            reply = L.chat_turn(runtime, "你好")
            self.assertEqual(reply, "你好，我是 Chris。")
            hits = runtime.memory.search("你好")
            self.assertTrue(hits, "conversation should be written to memory")

    def test_chat_turn_sends_real_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = self._runtime(tmp)
            seen = []

            class RecordingProvider:
                name = "rec"

                def generate(self, messages, **kwargs):
                    seen.append(messages)
                    return "ok"

            runtime.provider = RecordingProvider()
            history = [
                {"role": "user", "content": "a"},
                {"role": "assistant", "content": "b"},
            ]
            L.chat_turn(runtime, "c", history=history)
            sent = seen[-1]  # last generate call is the actual chat reply (first is the intent judge)
            self.assertEqual(
                [m["role"] for m in sent], ["system", "user", "assistant", "user"]
            )
            self.assertEqual(sent[-1]["content"], "c")

    def test_chat_turn_respects_cancel(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = self._runtime(tmp)
            cancel = threading.Event()
            cancel.set()
            self.assertIsNone(L.chat_turn(runtime, "你好", cancel=cancel))

    def test_chat_turn_projects_personality_and_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = self._runtime(tmp)
            from chris.app import build_system_prompt

            prompt = build_system_prompt(runtime)
            self.assertIn("Chris", prompt)
            self.assertIn("Current time:", prompt)

    def test_chat_turn_handles_no_action(self):
        from chris.behavior.engine import BehaviorAction, BehaviorDecision

        with tempfile.TemporaryDirectory() as tmp:
            runtime = self._runtime(tmp)
            runtime.behavior = type(
                "NoBehavior", (), {"decide": lambda self, ctx: BehaviorDecision(BehaviorAction.NO_ACTION)}
            )()
            self.assertIsNone(L.chat_turn(runtime, "?"))


if __name__ == "__main__":
    unittest.main()
