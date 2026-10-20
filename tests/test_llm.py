import unittest

from chris.config import LLMConfig
from chris.llm.base import LLMProvider
from chris.llm.factory import create_provider
from chris.llm.mock import MockProvider


class TestMockProvider(unittest.TestCase):
    def test_default_generate(self):
        provider = MockProvider()
        reply = provider.generate([{"role": "user", "content": "hello"}])
        self.assertIn("hello", reply)

    def test_scripted_responses_cycle(self):
        provider = MockProvider(responses=["a", "b"])
        self.assertEqual(provider.generate([]), "a")
        self.assertEqual(provider.generate([]), "b")
        self.assertEqual(provider.generate([]), "a")

    def test_stream_joins_to_full_reply(self):
        provider = MockProvider(responses=["one two three"])
        chunks = list(provider.stream([]))
        self.assertEqual("".join(chunks).strip(), "one two three")

    def test_health_check(self):
        self.assertTrue(MockProvider().health_check())

    def test_health_report(self):
        report = MockProvider().health_report()
        self.assertTrue(report["ok"])
        self.assertEqual(report["model"], "mock-default")


class TestFactory(unittest.TestCase):
    def test_create_mock(self):
        provider = create_provider(LLMConfig(provider="mock", model="mock-default"))
        self.assertIsInstance(provider, MockProvider)
        self.assertIsInstance(provider, LLMProvider)

    def test_unknown_provider_raises(self):
        with self.assertRaises(ValueError):
            create_provider(LLMConfig(provider="nope"))


if __name__ == "__main__":
    unittest.main()
