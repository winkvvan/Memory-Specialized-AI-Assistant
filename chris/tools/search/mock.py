"""Deterministic mock search provider for tests and offline development.

No network. Returns a small fixed set of plausible results so the whole
web_search pipeline can be exercised without any real search API.
"""

from __future__ import annotations

from chris.tools.search.base import SearchItem, SearchProvider, SearchResult

DEFAULT_ITEMS = [
    SearchItem(
        title="Chris AI 2026 最新动态",
        url="https://example.com/chris-ai-2026",
        snippet="Chris AI 在 2026 年的最新发展情况总结。",
        source="example.com",
        published_at="2026-08-01",
    ),
    SearchItem(
        title="AI 新闻速递",
        url="https://example.com/ai-news",
        snippet="每天更新的 AI 行业新闻。",
        source="example.com",
        published_at="2026-08-10",
    ),
]


class MockSearchProvider(SearchProvider):
    name = "mock"

    def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        freshness: str | None = None,
        domains: list[str] | None = None,
        timeout: float = 10.0,
    ) -> SearchResult:
        items = DEFAULT_ITEMS[:max_results]
        return SearchResult(
            query=query, results=items, provider=self.name, raw_response=None
        )
