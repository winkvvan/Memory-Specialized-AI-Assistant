"""Phase 8.2 web search tests (offline; no real network required).

Covers: SearchProvider abstraction, MockSearchProvider, DuckDuckGo adapter
(mock HTTP), SearchError mapping, WebSearchTool (valid/invalid/empty query,
max_results, dedup, snippet truncation, error mapping), Tool Core integration
(Registry -> Executor -> Permission -> WebSearchTool), and the full
LLM -> ToolCall -> search -> ToolResult -> LLM loop with a fake LLM.
"""

import unittest
from datetime import datetime, timezone
from unittest import mock

from chris.config import Config
from chris.events import EventBus, EventType
from chris.llm.base import LLMResponse
from chris.tools import ToolCall, ToolContext
from chris.tools.executor import ToolExecutor
from chris.tools.permission import DefaultPermissionPolicy
from chris.tools.registry import ToolRegistry
from chris.tools.search import (
    MockSearchProvider,
    SearchError,
    SearchErrorCode,
    SearchItem,
    SearchResult,
    create_search_provider,
)
from chris.tools.search.adapters.duckduckgo import (
    DuckDuckGoSearchProvider,
    _ResultParser,
)
from chris.tools.web_search import WebSearchTool, deduplicate, normalize_url

DDG_HTML = """
<html><body>
<div class="result">
  <h2 class="result__title">
    <a class="result__a" href="https://example.com/ai-news-2026">
      AI 2026 最新新闻
    </a>
  </h2>
  <a class="result__snippet" href="https://example.com/ai-news-2026">
    This is the snippet for the AI 2026 news article, kept short.
  </a>
  <a class="result__url" href="https://example.com/ai-news-2026">
    example.com
  </a>
</div>
<div class="result">
  <h2 class="result__title">
    <a class="result__a" href="https://other.example/news/2">
      Second result title
    </a>
  </h2>
  <a class="result__snippet" href="https://other.example/news/2">
    Second snippet here.
  </a>
  <a class="result__url" href="https://other.example/news/2">
    other.example
  </a>
</div>
</body></html>
"""


def _context(**kwargs):
    return ToolContext(
        now=datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc), **kwargs
    )


class TestNormalizeUrl(unittest.TestCase):
    def test_strips_fragment_and_trailing_slash(self):
        self.assertEqual(
            normalize_url("https://example.com/Path/#frag"),
            "https://example.com/Path",
        )

    def test_lowercases_host(self):
        self.assertEqual(
            normalize_url("https://EXAMPLE.COM/A"), "https://example.com/A"
        )

    def test_default_port_removed(self):
        self.assertEqual(
            normalize_url("https://example.com:443/a"), "https://example.com/a"
        )
        self.assertEqual(
            normalize_url("http://example.com:80/a"), "http://example.com/a"
        )

    def test_keeps_nondefault_port(self):
        self.assertEqual(
            normalize_url("http://example.com:8080/a"), "http://example.com:8080/a"
        )


class TestDeduplicate(unittest.TestCase):
    def test_dedup_by_normalized_url(self):
        items = [
            SearchItem(title="A", url="https://example.com/x/"),
            SearchItem(title="A dup", url="https://example.com/x"),
            SearchItem(title="B", url="https://other.com/b"),
        ]
        out = deduplicate(items)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0].title, "A")

    def test_dedup_by_exact_title(self):
        items = [
            SearchItem(title="Same title", url="https://a.com/1"),
            SearchItem(title="Same title", url="https://b.com/2"),
        ]
        out = deduplicate(items)
        self.assertEqual(len(out), 1)


class TestMockSearchProvider(unittest.TestCase):
    def test_search_returns_structured_result(self):
        provider = MockSearchProvider()
        result = provider.search("AI 新闻")
        self.assertIsInstance(result, SearchResult)
        self.assertEqual(result.query, "AI 新闻")
        self.assertTrue(result.results)
        first = result.results[0]
        self.assertIsInstance(first, SearchItem)
        self.assertTrue(first.title)
        self.assertTrue(first.url)

    def test_max_results_respected(self):
        provider = MockSearchProvider()
        result = provider.search("x", max_results=1)
        self.assertEqual(len(result.results), 1)


class TestFactory(unittest.TestCase):
    def test_default_provider_is_mock(self):
        provider = create_search_provider({})
        self.assertIsInstance(provider, MockSearchProvider)

    def test_duckduckgo_provider_selectable(self):
        provider = create_search_provider({"provider": "duckduckgo"})
        self.assertIsInstance(provider, DuckDuckGoSearchProvider)

    def test_unknown_provider_raises(self):
        with self.assertRaises(ValueError):
            create_search_provider({"provider": "nope"})


class TestDuckDuckGoAdapter(unittest.TestCase):
    def _response(self, text=DDG_HTML, status=200):
        response = mock.Mock()
        response.ok = status < 400
        response.status_code = status
        response.text = text
        return response

    def test_parses_structured_items(self):
        parser = _ResultParser()
        parser.feed(DDG_HTML)
        parser.close()
        self.assertEqual(len(parser.items), 2)
        title, url, snippet, source = parser.items[0]
        self.assertEqual(title, "AI 2026 最新新闻")
        self.assertEqual(url, "https://example.com/ai-news-2026")
        self.assertTrue(snippet)

    @mock.patch("chris.tools.search.adapters.duckduckgo.requests.get")
    def test_search_success(self, get):
        get.return_value = self._response()
        provider = DuckDuckGoSearchProvider()
        result = provider.search("AI 新闻", max_results=1)
        self.assertEqual(result.provider, "duckduckgo")
        self.assertEqual(len(result.results), 1)
        self.assertEqual(result.results[0].url, "https://example.com/ai-news-2026")

    @mock.patch("chris.tools.search.adapters.duckduckgo.requests.get")
    def test_timeout_maps_to_search_error(self, get):
        import requests as req

        get.side_effect = req.Timeout("slow")
        provider = DuckDuckGoSearchProvider()
        with self.assertRaises(SearchError) as ctx:
            provider.search("x")
        self.assertEqual(ctx.exception.code, SearchErrorCode.TIMEOUT)

    @mock.patch("chris.tools.search.adapters.duckduckgo.requests.get")
    def test_rate_limit_maps(self, get):
        get.return_value = self._response(status=429)
        with self.assertRaises(SearchError) as ctx:
            DuckDuckGoSearchProvider().search("x")
        self.assertEqual(ctx.exception.code, SearchErrorCode.RATE_LIMITED)

    @mock.patch("chris.tools.search.adapters.duckduckgo.requests.get")
    def test_auth_error_maps(self, get):
        get.return_value = self._response(status=403)
        with self.assertRaises(SearchError) as ctx:
            DuckDuckGoSearchProvider().search("x")
        self.assertEqual(ctx.exception.code, SearchErrorCode.AUTHENTICATION_ERROR)

    @mock.patch("chris.tools.search.adapters.duckduckgo.requests.get")
    def test_parse_error_on_bad_html(self, get):
        class Boom:
            text = ""

        get.side_effect = None
        get.return_value = self._response(text="<unclosed")  # still parseable; use monkeypatch instead
        # force a parse failure by patching the parser feed
        with mock.patch.object(_ResultParser, "feed", side_effect=RuntimeError("bad html")):
            with self.assertRaises(SearchError) as ctx:
                DuckDuckGoSearchProvider().search("x")
            self.assertEqual(ctx.exception.code, SearchErrorCode.PARSE_ERROR)


class TestWebSearchTool(unittest.TestCase):
    def _tool(self, provider=None, max_results=5, snippet_chars=300):
        return WebSearchTool(
            provider=provider or MockSearchProvider(),
            max_results=max_results,
            snippet_chars=snippet_chars,
        )

    def test_valid_query_success(self):
        result = self._tool().execute(_context(), {"query": "AI 新闻"})
        self.assertTrue(result.ok)
        self.assertEqual(result.status.value, "success")
        self.assertIn("Search results for 'AI 新闻'", result.content)
        self.assertIn("URL:", result.content)
        self.assertEqual(result.metadata["provider"], "mock")

    def test_empty_query_failure(self):
        result = self._tool().execute(_context(), {"query": "  "})
        self.assertEqual(result.status.value, "failure")
        self.assertIn("non-empty", result.error)

    def test_missing_query_failure(self):
        result = self._tool().execute(_context(), {})
        self.assertEqual(result.status.value, "failure")

    def test_query_too_long_failure(self):
        result = self._tool().execute(_context(), {"query": "x" * 501})
        self.assertEqual(result.status.value, "failure")
        self.assertIn("too long", result.error)

    def test_max_results_respected(self):
        tool = self._tool(max_results=1)
        result = tool.execute(_context(), {"query": "AI"})
        self.assertTrue(result.ok)
        self.assertEqual(result.metadata["result_count"], 1)

    def test_snippet_truncated(self):
        long_snippet = SearchItem(
            title="T", url="https://x.com/1", snippet="s" * 1000
        )
        provider = mock.Mock()
        provider.name = "mock"
        provider.search.return_value = SearchResult(query="q", results=[long_snippet], provider="mock")
        result = self._tool(provider=provider, snippet_chars=100).execute(
            _context(), {"query": "q"}
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.metadata["result_count"], 1)
        self.assertIn("…", result.content)
        self.assertLess(len(result.content), 500)

    def test_dedup_applied(self):
        provider = mock.Mock()
        provider.name = "mock"
        provider.search.return_value = SearchResult(
            query="q",
            results=[
                SearchItem(title="A", url="https://x.com/1"),
                SearchItem(title="A dup", url="https://x.com/1/"),
            ],
            provider="mock",
        )
        result = self._tool(provider=provider).execute(_context(), {"query": "q"})
        self.assertTrue(result.ok)
        self.assertEqual(result.metadata["result_count"], 1)
        self.assertEqual(result.metadata["deduplicated"], 1)

    def test_provider_error_maps_to_failure(self):
        provider = mock.Mock()
        provider.name = "mock"
        provider.search.side_effect = SearchError(SearchErrorCode.RATE_LIMITED, "nope")
        result = self._tool(provider=provider).execute(_context(), {"query": "q"})
        self.assertEqual(result.status.value, "failure")
        self.assertIn("rate limit", result.error)
        self.assertEqual(result.metadata["error_code"], "rate_limited")

    def test_unknown_exception_maps_to_failure(self):
        provider = mock.Mock()
        provider.name = "mock"
        provider.search.side_effect = RuntimeError("boom")
        result = self._tool(provider=provider).execute(_context(), {"query": "q"})
        self.assertEqual(result.status.value, "failure")
        self.assertIn("boom", result.error)

    def test_definition_exposes_query_schema(self):
        definition = self._tool().definition()
        self.assertEqual(definition.name, "web_search")
        self.assertIn("query", definition.input_schema["properties"])
        self.assertEqual(definition.input_schema["required"], ["query"])
        self.assertEqual(definition.risk_level.value, "READ_ONLY")

    def test_raw_vendor_data_never_in_content(self):
        provider = mock.Mock()
        provider.name = "mock"
        provider.search.return_value = SearchResult(
            query="q",
            results=[SearchItem(title="A", url="https://x.com/1")],
            provider="mock",
            raw_response={"secret": "vendor-stuff"},
        )
        result = self._tool(provider=provider).execute(_context(), {"query": "q"})
        self.assertTrue(result.ok)
        self.assertNotIn("vendor-stuff", result.content)


class TestWebSearchToolCoreIntegration(unittest.TestCase):
    def _registry_with_web(self):
        registry = ToolRegistry()
        registry.register(WebSearchTool(provider=MockSearchProvider()))
        return registry

    def test_executor_runs_web_search_via_registry(self):
        registry = self._registry_with_web()
        executor = ToolExecutor(registry, bus=EventBus())
        result = executor.execute(ToolCall.new("web_search", {"query": "AI 新闻"}), _context())
        self.assertTrue(result.ok)
        self.assertIn("Search results", result.content)

    def test_permission_readonly_allows_web_search(self):
        policy = DefaultPermissionPolicy()
        definition = WebSearchTool(provider=MockSearchProvider()).definition()
        decision = policy.check(definition, _context())
        self.assertTrue(decision.allowed)

    def test_events_emitted(self):
        registry = self._registry_with_web()
        bus = EventBus()
        seen = []
        bus.subscribe(seen.append, [EventType.TOOL_CALL_STARTED, EventType.TOOL_CALL_COMPLETED])
        executor = ToolExecutor(registry, bus=bus)
        executor.execute(ToolCall.new("web_search", {"query": "AI"}), _context())
        types = [e.type for e in seen]
        self.assertIn(EventType.TOOL_CALL_STARTED, types)
        self.assertIn(EventType.TOOL_CALL_COMPLETED, types)


class _SearchingLLM:
    """Fake LLM: first round emits a web_search tool call, second round answers."""

    def __init__(self, final="根据搜索，今天最大的 AI 新闻是……"):
        self._final = final
        self.rounds = 0
        self.last_messages = None

    def generate_with_tools(self, messages, tools=None, **kwargs):
        self.rounds += 1
        self.last_messages = list(messages)
        if self.rounds == 1:
            return LLMResponse(
                content="",
                tool_calls=[{"id": "w1", "name": "web_search", "arguments": {"query": "2026 AI 新闻"}}],
            )
        return LLMResponse(content=self._final)

    def generate(self, messages, **kwargs):
        return self._final


class _StubRuntime:
    def __init__(self):
        registry = ToolRegistry()
        registry.register(WebSearchTool(provider=MockSearchProvider()))
        executor = ToolExecutor(registry, bus=EventBus())
        self.tool_registry = registry
        self.tool_executor = executor
        self.config = Config()
        self.config.tools = {"enabled": True, "timeout": 30.0}
        self.bus = EventBus()
        self.temporal = mock.Mock()
        self.temporal.now.return_value = datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc)
        self.temporal.session_active.return_value = True
        self.memory = mock.Mock()
        self.memory.list_recent.return_value = []
        self.world = mock.Mock()
        self.world.location = "home"
        self.world.current_activity = "idle"
        self.world.user_presence = True


class TestWebSearchPipeline(unittest.TestCase):
    def test_full_loop_llm_to_search_to_answer(self):
        from chris.tools.pipeline import run_tool_pipeline

        runtime = _StubRuntime()
        runtime.provider = _SearchingLLM()
        reply = run_tool_pipeline(
            runtime, [{"role": "user", "content": "今天 AI 有什么新闻？"}]
        )
        self.assertEqual(reply, "根据搜索，今天最大的 AI 新闻是……")
        self.assertEqual(runtime.provider.rounds, 2)
        # tool result was fed back as a "tool" message
        roles = [m["role"] for m in runtime.provider.last_messages]
        self.assertIn("tool", roles)
        self.assertIn("Search results", runtime.provider.last_messages[-1]["content"])


if __name__ == "__main__":
    unittest.main()
