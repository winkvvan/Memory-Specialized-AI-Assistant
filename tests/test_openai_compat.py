import unittest
from unittest import mock

from chris.config import LLMConfig
from chris.llm.base import LLMProvider
from chris.llm.factory import create_provider
from chris.llm.openai_compat import (
    DeepSeekProvider,
    GLMProvider,
    MoonshotProvider,
    OpenAICompatProvider,
    QwenProvider,
)

VENDORS = [
    (DeepSeekProvider, "deepseek", "https://api.deepseek.com", "deepseek-v4-flash"),
    (QwenProvider, "qwen", "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
    (MoonshotProvider, "moonshot", "https://api.moonshot.cn/v1", "moonshot-v1-8k"),
    (GLMProvider, "glm", "https://open.bigmodel.cn/api/paas/v4", "glm-4-flash"),
]


class FakeResponse:
    def __init__(self, status=200, data=None, lines=None, text=""):
        self.status_code = status
        self.ok = status < 400
        self._data = data or {}
        self._lines = lines or []
        self.text = text or f"HTTP {status}"

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._data

    def iter_lines(self, decode_unicode=False):
        return iter(self._lines)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _chat_response(content):
    return {"choices": [{"message": {"content": content}}]}


class TestOpenAICompatProvider(unittest.TestCase):
    def _provider(self):
        return OpenAICompatProvider(
            base_url="https://example.com/v1", api_key="test-key", model="test-model"
        )

    @mock.patch("chris.llm.openai_compat.requests.post")
    def test_generate_parses_reply(self, post):
        post.return_value = FakeResponse(data=_chat_response("hello world"))
        reply = self._provider().generate([{"role": "user", "content": "hi"}])
        self.assertEqual(reply, "hello world")
        request = post.call_args
        self.assertEqual(request.args[0], "https://example.com/v1/chat/completions")
        body = request.kwargs["json"]
        self.assertEqual(body["model"], "test-model")
        self.assertEqual(body["messages"], [{"role": "user", "content": "hi"}])

    @mock.patch("chris.llm.openai_compat.requests.post")
    def test_generate_passes_max_tokens(self, post):
        post.return_value = FakeResponse(data=_chat_response("ok"))
        self._provider().generate([], max_tokens=256)
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["max_tokens"], 256)

    @mock.patch("chris.llm.openai_compat.requests.post")
    def test_generate_retries_on_empty_content(self, post):
        empty = FakeResponse(data={"choices": [{"message": {"content": ""}}]})
        ok = FakeResponse(data=_chat_response("finally"))
        post.side_effect = [empty, ok]
        reply = self._provider().generate([])
        self.assertEqual(reply, "finally")
        self.assertEqual(post.call_count, 2)

    @mock.patch("chris.llm.openai_compat.requests.post")
    def test_generate_raises_when_always_empty(self, post):
        empty = FakeResponse(data={"choices": [{"message": {"content": "  "}}]})
        post.return_value = empty
        with self.assertRaises(RuntimeError):
            self._provider().generate([])
        self.assertEqual(post.call_count, 2)

    @mock.patch("chris.llm.openai_compat.os.environ", {})
    def test_generate_missing_key_raises(self):
        provider = OpenAICompatProvider(base_url="https://example.com/v1", api_key=None)
        with self.assertRaises(ValueError):
            provider.generate([])

    @mock.patch("chris.llm.openai_compat.requests.post")
    def test_stream_parses_sse(self, post):
        lines = [
            'data: {"choices":[{"delta":{"content":"Hel"}}]}',
            'data: {"choices":[{"delta":{"content":"lo"}}]}',
            "data: [DONE]",
        ]
        post.return_value = FakeResponse(lines=lines)
        chunks = list(self._provider().stream([]))
        self.assertEqual("".join(chunks), "Hello")

    @mock.patch("chris.llm.openai_compat.requests.get")
    def test_health_check_ok(self, get):
        get.return_value = FakeResponse(status=200)
        self.assertTrue(self._provider().health_check())

    @mock.patch("chris.llm.openai_compat.requests.get")
    def test_health_check_failure(self, get):
        get.return_value = FakeResponse(status=401)
        self.assertFalse(self._provider().health_check())

    @mock.patch("chris.llm.openai_compat.requests.get")
    def test_health_report_ok(self, get):
        get.return_value = FakeResponse(status=200)
        report = self._provider().health_report()
        self.assertTrue(report["ok"])
        self.assertEqual(report["status"], 200)
        self.assertEqual(report["model"], "test-model")
        self.assertIsNone(report["error"])

    @mock.patch("chris.llm.openai_compat.requests.get")
    def test_health_report_unauthorized(self, get):
        get.return_value = FakeResponse(status=401)
        report = self._provider().health_report()
        self.assertFalse(report["ok"])
        self.assertIsNotNone(report["error"])

    @mock.patch("chris.llm.openai_compat.requests.get")
    def test_health_report_network_error(self, get):
        import requests

        get.side_effect = requests.ConnectionError("connection refused")
        report = self._provider().health_report()
        self.assertFalse(report["ok"])
        self.assertIsNone(report["status"])
        self.assertIn("connection refused", report["error"])


class TestVendorProviders(unittest.TestCase):
    @mock.patch("chris.llm.openai_compat.os.environ", {})
    def test_defaults(self):
        for provider_cls, _name, base_url, model in VENDORS:
            with self.subTest(provider=provider_cls.__name__):
                provider = provider_cls(api_key="k")
                self.assertEqual(provider.base_url, base_url)
                self.assertEqual(provider.model, model)
                self.assertIsInstance(provider, LLMProvider)

    @mock.patch("chris.llm.openai_compat.os.environ", {})
    def test_requires_key(self):
        for provider_cls, _name, _base_url, _model in VENDORS:
            with self.subTest(provider=provider_cls.__name__):
                with self.assertRaises(ValueError):
                    provider_cls().generate([])

    def test_override_model(self):
        provider = QwenProvider(api_key="k", model="qwen-max")
        self.assertEqual(provider.model, "qwen-max")

    def test_override_base_url(self):
        provider = MoonshotProvider(api_key="k", base_url="http://localhost:8000/v1")
        self.assertEqual(provider.base_url, "http://localhost:8000/v1")


class TestFactory(unittest.TestCase):
    def test_create_deepseek(self):
        provider = create_provider(LLMConfig(provider="deepseek"))
        self.assertIsInstance(provider, DeepSeekProvider)

    def test_create_deepseek_uses_config_model(self):
        provider = create_provider(LLMConfig(provider="deepseek", model="deepseek-reasoner"))
        self.assertEqual(provider.model, "deepseek-reasoner")

    def test_create_openai_compat(self):
        provider = create_provider(
            LLMConfig(
                provider="openai_compat",
                model="gpt-4o-mini",
                options={"base_url": "https://example.com/v1"},
            )
        )
        self.assertIsInstance(provider, OpenAICompatProvider)

    def test_create_vendors(self):
        for provider_cls, name, _base_url, _model in VENDORS:
            with self.subTest(provider=name):
                provider = create_provider(LLMConfig(provider=name))
                self.assertIsInstance(provider, provider_cls)


if __name__ == "__main__":
    unittest.main()
