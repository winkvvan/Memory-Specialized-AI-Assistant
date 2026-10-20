"""Search provider factory: build a provider from the existing config system.

The provider is chosen by name (e.g. "mock" / "duckduckgo"); adding a new
provider is just registering a name -> class mapping here. No business module
ever imports a concrete provider directly.
"""

from __future__ import annotations

from typing import Any

from chris.tools.search.base import SearchProvider
from chris.tools.search.mock import MockSearchProvider

_REGISTRY: dict[str, type[SearchProvider]] = {}


def register(name: str, provider_cls: type[SearchProvider]) -> None:
    _REGISTRY[name] = provider_cls


def create_search_provider(config: dict[str, Any] | None = None) -> SearchProvider:
    """Create a search provider from a config dict (the `tools.web_search` section).

    Raises ValueError for unknown provider names so misconfiguration is loud.
    """
    config = config or {}
    name = config.get("provider") or "mock"
    provider_cls = _REGISTRY.get(name)
    if provider_cls is None:
        raise ValueError(
            f"Unknown search provider: {name!r}. Registered: {sorted(_REGISTRY)}"
        )
    return provider_cls(**config)


register("mock", MockSearchProvider)


def _register_builtin_http_providers() -> None:
    """Import concrete HTTP adapters lazily (they import `requests`, which is
    only allowed inside the exempt adapters directory)."""
    from chris.tools.search.adapters.duckduckgo import DuckDuckGoSearchProvider

    register("duckduckgo", DuckDuckGoSearchProvider)


_register_builtin_http_providers()
