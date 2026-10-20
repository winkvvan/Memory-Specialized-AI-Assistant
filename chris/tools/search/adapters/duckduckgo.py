"""DuckDuckGo web search adapter.

Keyless (legal and stable for a personal assistant), uses the HTML search
endpoint and the stdlib HTMLParser - no new dependency beyond `requests`.
Raw HTML is parsed into structured :class:`SearchItem` objects; the raw page
is retained in ``SearchResult.raw_response`` for debugging only.

This directory is exempt from the business-code HTTP import guardrail (same as
`chris/llm/` and `chris/embedding/adapters/`).
"""

from __future__ import annotations

import html
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests

from chris.tools.search.base import (
    SearchError,
    SearchErrorCode,
    SearchItem,
    SearchProvider,
    SearchResult,
)

DEFAULT_ENDPOINT = "https://html.duckduckgo.com/html/"


class _ResultParser(HTMLParser):
    """Parse DDG HTML result page into (title, url, snippet, source) tuples."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[tuple[str, str, str, str]] = []
        self._url = ""
        self._source = ""
        self._in_title = False
        self._in_snippet = False
        self._title_buf: list[str] = []
        self._snippet_buf: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attrs = dict(attrs)
        classes = (attrs.get("class") or "").split()
        if tag == "a" and "result__a" in classes:
            self._commit()
            self._in_title = True
            self._title_buf = []
            self._snippet_buf = []
            self._url = self._normalize_href(attrs.get("href") or "")
        elif tag == "a" and "result__snippet" in classes:
            self._in_snippet = True
            self._snippet_buf = []
        elif tag == "a" and "result__url" in classes:
            self._source = self._extract_source(self._url)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_title:
            self._in_title = False
        elif tag == "a" and self._in_snippet:
            self._in_snippet = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_buf.append(data)
        elif self._in_snippet:
            self._snippet_buf.append(data)

    def _commit(self) -> None:
        title = " ".join("".join(self._title_buf).split())
        snippet = " ".join("".join(self._snippet_buf).split())
        if title and self._url:
            self.items.append((title, self._url, snippet, self._source))
        self._url = ""
        self._source = ""
        self._title_buf = []
        self._snippet_buf = []
        self._in_title = False
        self._in_snippet = False

    def close(self) -> None:
        self._commit()
        super().close()

    @staticmethod
    def _extract_source(url: str) -> str:
        parsed = urlparse(url)
        return parsed.netloc or parsed.path

    @staticmethod
    def _normalize_href(href: str) -> str:
        # DDG wraps real URLs in /l/?uddg=<encoded>
        if href.startswith("/l/?uddg="):
            decoded = parse_qs(urlparse(href).query).get("uddg", [""])[0]
            return html.unescape(decoded) or href
        return html.unescape(href)


class DuckDuckGoSearchProvider(SearchProvider):
    name = "duckduckgo"

    def __init__(
        self,
        endpoint: str = DEFAULT_ENDPOINT,
        timeout: float = 10.0,
        headers: dict[str, str] | None = None,
        **_: Any,
    ) -> None:
        self.endpoint = endpoint
        self.timeout = timeout
        self.headers = headers or {
            "User-Agent": "Mozilla/5.0 (compatible; ChrisPersonalAssistant/1.0)"
        }

    def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        freshness: str | None = None,
        domains: list[str] | None = None,
        timeout: float | None = None,
    ) -> SearchResult:
        params = {"q": query}
        if freshness:
            params["df"] = freshness
        try:
            response = requests.get(
                self.endpoint,
                params=params,
                headers=self.headers,
                timeout=timeout or self.timeout,
            )
        except requests.Timeout as exc:
            raise SearchError(SearchErrorCode.TIMEOUT, "search request timed out", detail=str(exc)) from exc
        except requests.RequestException as exc:
            raise SearchError(
                SearchErrorCode.PROVIDER_UNAVAILABLE, "search provider unavailable", detail=str(exc)
            ) from exc

        if response.status_code == 429 or response.status_code == 503:
            raise SearchError(SearchErrorCode.RATE_LIMITED, "search rate limited")
        if response.status_code in (401, 403):
            raise SearchError(SearchErrorCode.AUTHENTICATION_ERROR, "search authentication failed")
        if response.status_code == 400:
            raise SearchError(SearchErrorCode.INVALID_REQUEST, "search request invalid")
        if not response.ok:
            raise SearchError(
                SearchErrorCode.PROVIDER_UNAVAILABLE,
                f"search provider error HTTP {response.status_code}",
            )

        try:
            parser = _ResultParser()
            parser.feed(response.text)
        except Exception as exc:  # noqa: BLE001
            raise SearchError(SearchErrorCode.PARSE_ERROR, "failed to parse search results", detail=str(exc)) from exc

        items = [
            SearchItem(
                title=title,
                url=url,
                snippet=snippet,
                source=source,
            )
            for title, url, snippet, source in parser.items[:max_results]
        ]
        return SearchResult(
            query=query,
            results=items,
            provider=self.name,
            raw_response={"html": response.text[:2000], "status": response.status_code},
        )
