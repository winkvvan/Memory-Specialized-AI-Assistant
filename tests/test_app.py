import tempfile
import unittest
from pathlib import Path

from chris.app import (
    build_runtime,
    build_system_prompt,
    extract_remember,
    extract_remember_with_llm,
    has_remember_intent,
)
from chris.config import Config
from chris.llm.base import LLMProvider
from chris.llm.mock import MockProvider


class TestBuildRuntime(unittest.TestCase):
    def test_build_runtime_wires_all_systems(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Config.load()
            config.personality_file = str(Path(tmp) / "p.json")
            config.memory_file = str(Path(tmp) / "m.json")
            config.temporal_file = str(Path(tmp) / "t.json")
            config.world_state_file = str(Path(tmp) / "w.json")
            runtime = build_runtime(config)
            self.assertIsInstance(runtime.provider, LLMProvider)
            self.assertIsNotNone(runtime.personality)
            self.assertIsNotNone(runtime.memory)
            self.assertIsNotNone(runtime.temporal)
            self.assertIsNotNone(runtime.world)
            self.assertIsNotNone(runtime.behavior)

    def test_system_prompt_includes_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Config()
            config.personality_file = str(Path(tmp) / "p.json")
            config.memory_file = str(Path(tmp) / "m.json")
            config.temporal_file = str(Path(tmp) / "t.json")
            config.world_state_file = str(Path(tmp) / "w.json")
            runtime = build_runtime(config)
            prompt = build_system_prompt(runtime)
            self.assertIn("Chris", prompt)
            self.assertIn("中文（简体）", prompt)

    def test_provider_override(self):
        provider = MockProvider(responses=["fixed"])
        with tempfile.TemporaryDirectory() as tmp:
            config = Config()
            config.memory_file = str(Path(tmp) / "m.json")
            runtime = build_runtime(config, provider=provider)
            self.assertEqual(runtime.provider.generate([]), "fixed")

    def test_test_mode_routes_memory_to_test_library(self):
        from pathlib import Path as P

        project_root = P(__file__).resolve().parent.parent
        config = Config.load()
        config.test_mode = True
        runtime = build_runtime(config)
        self.assertEqual(runtime.memory.path, project_root / "data" / "test" / "memory.json")

    def test_normal_mode_uses_configured_memory_file(self):
        config = Config.load()
        config.test_mode = False
        runtime = build_runtime(config)
        self.assertEqual(runtime.memory.path, Config.resolve(config, config.memory_file))


class TestExtractRemember(unittest.TestCase):
    def test_extracts_after_marker(self):
        self.assertEqual(extract_remember("记住我喜欢喝咖啡"), "我喜欢喝咖啡")

    def test_extracts_before_marker(self):
        self.assertEqual(extract_remember("我喜欢玩女神异闻录5，记住了"), "我喜欢玩女神异闻录5")

    def test_returns_none_without_marker(self):
        self.assertIsNone(extract_remember("今天天气不错"))

    def test_returns_none_for_marker_only(self):
        self.assertIsNone(extract_remember("记住"))

    def test_questions_do_not_create_memories(self):
        self.assertIsNone(extract_remember("我之前让你记住的对象你还记得吗"))
        self.assertIsNone(extract_remember("你还记得我让你记住什么吗"))
        self.assertIsNone(extract_remember("我们认识多久了？"))
        self.assertIsNone(extract_remember("你记得我生日吗"))

    def test_question_with_directive_still_extracts(self):
        self.assertEqual(extract_remember("记住我对象叫小明"), "我对象叫小明")


class _StubProvider:
    def __init__(self, reply):
        self._reply = reply

    def generate(self, messages, **kwargs):
        return self._reply


class TestExtractRememberWithLLM(unittest.TestCase):
    def test_has_remember_intent(self):
        self.assertTrue(has_remember_intent("记住我喜欢喝咖啡"))
        self.assertTrue(has_remember_intent("别忘了带伞"))
        self.assertFalse(has_remember_intent("今天天气不错"))
        # 问句含"记住"也走 LLM 判断（由 LLM 决定 NO_MEMORY）
        self.assertTrue(has_remember_intent("你还记得我让你记住什么吗"))

    def test_llm_extracts_facts(self):
        provider = _StubProvider("我对象叫小明\n我喜欢喝冰美式")
        facts = extract_remember_with_llm(provider, "记住我对象叫小明，我喜欢喝冰美式")
        self.assertEqual(facts, ["我对象叫小明", "我喜欢喝冰美式"])

    def test_llm_returns_no_memory_for_question(self):
        provider = _StubProvider("NO_MEMORY")
        facts = extract_remember_with_llm(provider, "我之前让你记住的对象你还记得吗")
        self.assertEqual(facts, [])

    def test_no_intent_skips_llm(self):
        provider = _StubProvider("should not be called")
        self.assertEqual(extract_remember_with_llm(provider, "今天天气不错"), [])


if __name__ == "__main__":
    unittest.main()
