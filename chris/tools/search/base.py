"""Search models and the provider interface.

The structured data here is what Chris Core (and therefore the LLM) sees. Raw
vendor JSON stays inside the concrete provider as ``raw_response`` and is only
used for debugging / provider metadata - never as ToolResult.content.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SearchErrorCode(str, Enum):
    INVALID_REQUEST = "invalid_request"
    AUTHENTICATION_ERROR = "authentication_error"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PARSE_ERROR = "parse_error"
    UNKNOWN = "unknown"


class SearchError(Exception):
    """Unified, vendor-independent search failure."""

    def __init__(
        self,
        code: SearchErrorCode,
        message: str,
        *,
        detail: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "message": self.message,
        }


@dataclass(frozen=True)
class SearchItem:
    """One structured web search result."""

    title: str
    url: str
    snippet: str = ""
    source: str = ""
    published_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "source": self.source,
            "published_at": self.published_at,
        }


@dataclass
class SearchResult:
    """Structured, deduplicated search response."""

    query: str
    results: list[SearchItem] = field(default_factory=list)
    provider: str = ""
    raw_response: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "provider": self.provider,
            "results": [item.to_dict() for item in self.results],
        }

    def to_text(self) -> str:
        """Human/LLM-friendly projection of the results.

        Never includes raw vendor JSON. Used as the ToolResult content fed back
        into the conversation.
        """
        lines = [f"Search results for {self.query!r}:"]
        for index, item in enumerate(self.results, 1):
            lines.append(f"{index}. {item.title}")
            if item.url:
                lines.append(f"   URL: {item.url}")
            if item.source:
                lines.append(f"   Source: {item.source}")
            if item.published_at:
                lines.append(f"   Published: {item.published_at}")
            if item.snippet:
                lines.append(f"   Snippet: {item.snippet}")
        return "\n".join(lines)


class SearchProvider(ABC):
    """Unified search interface. Sync, matching the Chris core architecture.

    Concrete providers must translate vendor errors into :class:`SearchError`
    with a :class:`SearchErrorCode`; raw vendor response is kept in
    ``SearchResult.raw_response`` (not surfaced to the LLM).
    """

    name: str = "abstract"

    @abstractmethod
    def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        freshness: str | None = None,
        domains: list[str] | None = None,
        timeout: float = 10.0,
    ) -> SearchResult:
        """Search the web for `query` and return structured results."""
