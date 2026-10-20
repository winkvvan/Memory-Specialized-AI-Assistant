"""Web search subsystem for Chris.

Phase 8.2: an abstract, swappable search layer feeding the Tool Core. Chris Core
only deals with structured :class:`SearchResult` / :class:`SearchItem`; raw
vendor responses never reach the LLM. Providers (DuckDuckGo, future APIs) are
selected through the existing config system, so switching the search backend
never changes Chris Core or the web_search tool.
"""

from chris.tools.search.base import (
    SearchError,
    SearchErrorCode,
    SearchItem,
    SearchProvider,
    SearchResult,
)
from chris.tools.search.factory import create_search_provider
from chris.tools.search.mock import MockSearchProvider

__all__ = [
    "MockSearchProvider",
    "SearchError",
    "SearchErrorCode",
    "SearchItem",
    "SearchProvider",
    "SearchResult",
    "create_search_provider",
]
