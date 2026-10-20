"""7.4 dialogue recall-intent tests (LLM-first, not keyword detection).

Verifies the MemoryRecallIntent flow: the LLM judges whether the user is
explicitly re-confirming a past memory (-> REINFORCE via MemoryPolicy) or
asking about old/archived memories ('你知不知道...' like phrasing, no keyword
list), and that access is still not reinforcement.
"""

import json
import tempfile
import unittest
from pathlib import Path

from chris.app import build_runtime, build_system_prompt, process_recall_intent
from chris.config import Config
from chris.memory.base import MemoryEntry
from chris.memory.ecology import StorageTier
from chris.llm.mock import MockProvider


def make_config(tmp):
    cfg = Config()
    cfg.clock_mode = "manual"
    cfg.personality_file = str(Path(tmp) / "p.json")
    cfg.memory_file = str(Path(tmp) / "m.json")
    cfg.temporal_file = str(Path(tmp) / "t.json")
    cfg.world_state_file = str(Path(tmp) / "w.json")
    return cfg


class FixedJudgeProvider:
    """Returns a fixed JSON decision for the intent judge, a canned reply after."""

    name = "judge"

    def __init__(self, decision: str, reply: str = "ok"):
        self._decision = decision
        self._reply = reply
        self.calls = 0

    def generate(self, messages, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return self._decision
        return self._reply

    def stream(self, messages, **kwargs):
        return iter([self.generate(messages, **kwargs)])

    def health_check(self):
        return True


def _runtime(tmp):
    runtime = build_runtime(make_config(tmp), provider=FixedJudgeProvider('{}'))
    entry = runtime.memory.add(
        MemoryEntry(kind="fact", content="用户喜欢冰美式", metadata={"skip_dedup": True})
    )
    return runtime, entry


def _judge(entry, confirm=True, want_old=False) -> str:
    return json.dumps(
        {
            "confirm_memory_ids": [entry.memory_id] if confirm else [],
            "want_old_memory": want_old,
        }
    )


class TestRecallIntentJudge(unittest.TestCase):
    def test_explicit_confirmation_reinforces(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime, entry = _runtime(tmp)
            runtime.provider = FixedJudgeProvider(_judge(entry))
            before = runtime.memory.get(entry.memory_id).life_score
            decision = process_recall_intent(runtime, "对，我就是喜欢冰美式", exclude_memory_ids=set())
            self.assertIn(entry.memory_id, decision.confirm_memory_ids)
            after = runtime.memory.get(entry.memory_id)
            self.assertGreater(after.life_score, before)
            self.assertEqual(after.reinforcement_count, 1)

    def test_mention_only_does_not_reinforce(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime, entry = _runtime(tmp)
            runtime.provider = FixedJudgeProvider(_judge(entry, confirm=False))
            before = runtime.memory.get(entry.memory_id).life_score
            decision = process_recall_intent(runtime, "冰美式是什么？", exclude_memory_ids=set())
            self.assertEqual(decision.confirm_memory_ids, [])
            after = runtime.memory.get(entry.memory_id)
            self.assertEqual(after.life_score, before)
            self.assertEqual(after.reinforcement_count, 0)

    def test_confirmed_archived_memory_recovers(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime, entry = _runtime(tmp)
            runtime.memory.archive_one(entry.memory_id)
            self.assertEqual(runtime.memory.get(entry.memory_id).storage_tier, StorageTier.ARCHIVE.value)
            runtime.provider = FixedJudgeProvider(_judge(entry))
            process_recall_intent(runtime, "对，就是那个", exclude_memory_ids=set())
            self.assertEqual(runtime.memory.get(entry.memory_id).storage_tier, StorageTier.ACTIVE.value)
            self.assertEqual(runtime.memory.get(entry.memory_id).status, "RECOVERED")

    def test_want_old_memory_includes_archived_in_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = build_runtime(make_config(tmp), provider=FixedJudgeProvider(
                '{"confirm_memory_ids": [], "want_old_memory": true}'
            ))
            runtime.memory.add(
                MemoryEntry(kind="fact", content="用户最近一直在开发 Chris",
                            metadata={"skip_dedup": True})
            )
            old = runtime.memory.add(
                MemoryEntry(kind="fact", content="之前那个 AI 项目",
                            metadata={"skip_dedup": True})
            )
            runtime.memory.archive_one(old.memory_id)
            decision = process_recall_intent(runtime, "你知不知道我之前那个AI项目", exclude_memory_ids=set())
            self.assertTrue(decision.want_old_memory)
            self.assertTrue(runtime._recall_include_archived)
            prompt = build_system_prompt(runtime, query_text="AI 项目")
            self.assertIn("之前那个 AI 项目", prompt)

    def test_judge_failure_is_safe_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime, entry = _runtime(tmp)
            runtime.provider = _FailingProvider()
            before = runtime.memory.get(entry.memory_id).life_score
            decision = process_recall_intent(runtime, "对，冰美式", exclude_memory_ids=set())
            self.assertEqual(decision.confirm_memory_ids, [])
            self.assertFalse(decision.want_old_memory)
            self.assertEqual(runtime.memory.get(entry.memory_id).life_score, before)
            self.assertFalse(getattr(runtime, "_recall_include_archived", False))

    def test_recall_counts_access_but_not_strength(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime, entry = _runtime(tmp)
            runtime.provider = FixedJudgeProvider(_judge(entry, confirm=False))
            before = runtime.memory.get(entry.memory_id).life_score
            process_recall_intent(runtime, "冰美式", exclude_memory_ids=set())
            after = runtime.memory.get(entry.memory_id)
            self.assertGreater(after.access_count, 0)
            self.assertEqual(after.life_score, before)


class _FailingProvider:
    name = "fail"

    def generate(self, messages, **kwargs):
        raise RuntimeError("provider down")

    def stream(self, messages, **kwargs):
        raise RuntimeError("provider down")

    def health_check(self):
        return False


class TestDialogueFlow(unittest.TestCase):
    def test_chat_turn_confirmation_reinforces_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_config(tmp)
            runtime = build_runtime(cfg, provider=MockProvider(responses=[
                '{"confirm_memory_ids": ["%s"], "want_old_memory": false}' % "PLACEHOLDER",
                "好的，我记得。",
            ]))
            entry = runtime.memory.add(
                MemoryEntry(kind="fact", content="用户喜欢冰美式", metadata={"skip_dedup": True})
            )
            runtime.provider._responses[0] = (
                '{"confirm_memory_ids": ["%s"], "want_old_memory": false}' % entry.memory_id
            )
            from launcher import chat as launcher_chat

            before = runtime.memory.get(entry.memory_id).life_score
            reply = launcher_chat.chat_turn(runtime, "对，我就是喜欢冰美式")
            self.assertEqual(reply, "好的，我记得。")
            after = runtime.memory.get(entry.memory_id)
            self.assertGreater(after.life_score, before)
            self.assertEqual(after.reinforcement_count, 1)


if __name__ == "__main__":
    unittest.main()
