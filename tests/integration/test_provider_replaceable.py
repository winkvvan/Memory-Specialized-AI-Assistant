"""Provider replaceability: business layer only depends on the LLMProvider interface."""

from __future__ import annotations

import unittest

from chris.config import LLMConfig
from chris.llm.base import LLMProvider
from chris.llm.factory import create_provider

PROVIDER_NAMES = ["mock", "openai_compat", "deepseek", "qwen", "moonshot", "glm"]


class TestProviderReplaceable(unittest.TestCase):
    def test_every_registered_provider_is_an_LLMProvider(self):
        for name in PROVIDER_NAMES:
            with self.subTest(provider=name):
                options = {}
                if name == "openai_compat":
                    options = {"base_url": "https://example.com/v1", "api_key": "k"}
                provider = create_provider(LLMConfig(provider=name, **({"model": "m"} if name != "mock" else {}), options=options))
                self.assertIsInstance(provider, LLMProvider)
                self.assertTrue(callable(provider.generate))
                self.assertTrue(callable(provider.stream))
                self.assertTrue(callable(provider.health_check))

    def test_unknown_provider_raises(self):
        with self.assertRaises(ValueError):
            create_provider(LLMConfig(provider="does-not-exist"))

    def test_business_layer_does_not_import_concrete_providers(self):
        import chris.app
        import chris.cli

        for module in (chris.app, chris.cli):
            with open(module.__file__, encoding="utf-8") as fh:
                text = fh.read()
            for forbidden in ("MockProvider", "DeepSeekProvider", "OpenAICompatProvider", "requests"):
                self.assertNotIn(forbidden, text, f"{module.__name__} must not import {forbidden}")


if __name__ == "__main__":
    unittest.main()
