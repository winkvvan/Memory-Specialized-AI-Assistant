"""web_search: the formal web search tool for Chris.

Registered through the ToolRegistry, executed through the ToolExecutor, gated by
the permission policy (READ_ONLY - searching the web does not require manual
confirmation) and schema validation. It delegates to a swappable
:class:`SearchProvider`, never talks to HTTP itself, and returns structured
:class:`SearchResult` text as ToolResult.content - raw vendor JSON stays inside
the provider's ``raw_response``.

Results are deduplicated by normalized URL and snippets truncated to fit the
LLM context budget. Search results are never written to long-term Memory; they
belong to the current conversation context only.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from chris.tools.base import Tool, ToolContext
from chris.tools.models import RiskLevel, ToolResult, ToolStatus
from chris.tools.search.base import SearchError, SearchItem, SearchProvider, SearchResult

MAX_QUERY_LENGTH = 500
DEFAULT_MAX_RESULTS = 5
MAX_MAX_RESULTS = 20
DEFAULT_SNIPPET_CHARS = 300


def normalize_url(url: str) -> str:
    """Normalize a URL for deduplication: lowercase scheme+host, strip
    fragment, trailing slash and default ports."""
    if not url:
        return url
    try:
        parts = urlsplit(url)
    except ValueError:
        return url.strip().rstrip("/").casefold()
    scheme = (parts.scheme or "").lower()
    host = (parts.hostname or "").lower()
    port = parts.port
    default_port = {"http": 80, "https": 443}.get(scheme)
    if port == default_port:
        port = None
    netloc = host if port is None else f"{host}:{port}"
    path = (parts.path or "").rstrip("/")
    query = parts.query
    key = f"{scheme}://{netloc}{path}"
    if query:
        key += f"?{query}"
    return key


def deduplicate(items: list[SearchItem]) -> list[SearchItem]:
    """Basic URL-based deduplication (plus a light exact-title guard)."""
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    out: list[SearchItem] = []
    for item in items:
        key = normalize_url(item.url)
        title = " ".join(item.title.split()).casefold()
        if key and key in seen_urls:
            continue
        if title and title in seen_titles:
            continue
        seen_urls.add(key)
        seen_titles.add(title)
        out.append(item)
    return out


class WebSearchTool(Tool):
    name = "web_search"
    description = (
        "Search the web for current, up-to-date information that Chris does not "
        "already know. Use this when the user asks about recent news, current "
        "events, live prices, or anything that may have changed after your "
        "training. Provide a clear, specific query."
    )
    permission = RiskLevel.READ_ONLY
    risk_level = RiskLevel.READ_ONLY
    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query (non-empty, concise, e.g. '2026 latest AI model releases').",
            },
            "max_results": {
                "type": "integer",
                "description": "Max results to return (1-20, default 5).",
                "minimum": 1,
                "maximum": MAX_MAX_RESULTS,
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        provider: SearchProvider,
        max_results: int = DEFAULT_MAX_RESULTS,
        snippet_chars: int = DEFAULT_SNIPPET_CHARS,
    ) -> None:
        self.provider = provider
        self.max_results = max_results
        self.snippet_chars = snippet_chars

    def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            return ToolResult(
                tool_name=self.name,
                status=ToolStatus.FAILURE,
                error="invalid search query: query must be a non-empty string",
            )
        query = query.strip()
        if len(query) > MAX_QUERY_LENGTH:
            return ToolResult(
                tool_name=self.name,
                status=ToolStatus.FAILURE,
                error=f"invalid search query: query too long ({len(query)} > {MAX_QUERY_LENGTH})",
            )

        requested = arguments.get("max_results")
        limit = self.max_results
        if isinstance(requested, int) and requested > 0:
            limit = min(requested, MAX_MAX_RESULTS)

        try:
            result = self.provider.search(
                query, max_results=limit, timeout=getattr(context, "timeout", None)
            )
        except SearchError as exc:
            return self._search_error_result(exc)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                tool_name=self.name,
                status=ToolStatus.FAILURE,
                error=f"search failed: {type(exc).__name__}: {exc}",
            )

        items = deduplicate(result.results)[:limit]

        def _truncate(item: SearchItem) -> SearchItem:
            if len(item.snippet) <= self.snippet_chars:
                return item
            return SearchItem(
                title=item.title,
                url=item.url,
                snippet=item.snippet[: self.snippet_chars].rstrip() + "…",
                source=item.source,
                published_at=item.published_at,
            )

        items = [_truncate(item) for item in items]

        cleaned = SearchResult(
            query=query,
            results=items,
            provider=result.provider,
            raw_response=None,  # raw vendor data never reaches the conversation
        )
        metadata = {
            "provider": result.provider,
            "query": query,
            "result_count": len(items),
            "deduplicated": len(result.results) - len(items),
        }
        return ToolResult(
            tool_name=self.name,
            status=ToolStatus.SUCCESS,
            content=cleaned.to_text(),
            metadata=metadata,
        )

    def _search_error_result(self, exc: SearchError) -> ToolResult:
        friendly = {
            "invalid_request": "invalid search request",
            "authentication_error": "search provider authentication failed",
            "rate_limited": "search provider rate limit exceeded",
            "timeout": "search request timed out",
            "provider_unavailable": "search provider unavailable",
            "parse_error": "failed to parse search results",
            "unknown": "search failed for an unknown reason",
        }
        return ToolResult(
            tool_name=self.name,
            status=ToolStatus.FAILURE,
            error=friendly.get(exc.code.value, exc.message),
            metadata={"error_code": exc.code.value},
        )
