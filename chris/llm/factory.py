"""Provider registry and factory.

New providers register here (e.g. OllamaProvider, OpenAIProvider) and are
selected by name in the config. No business module ever imports a provider
implementation directly.
"""

from __future__ import annotations

from typing import Type

from chris.config import LLMConfig
from chris.llm.base import LLMProvider
from chris.llm.mock import MockProvider
from chris.llm.openai_compat import (
    APIProvider,
    DeepSeekProvider,
    GLMProvider,
    MoonshotProvider,
    OpenAICompatProvider,
    QwenProvider,
)

_REGISTRY: dict[str, Type[LLMProvider]] = {}


def register(name: str, provider_cls: Type[LLMProvider]) -> None:
    _REGISTRY[name] = provider_cls


def create_provider(cfg: LLMConfig) -> LLMProvider:
    provider_cls = _REGISTRY.get(cfg.provider)
    if provider_cls is None:
        raise ValueError(
            f"Unknown LLM provider: {cfg.provider!r}. Registered: {sorted(_REGISTRY)}"
        )
    options = dict(cfg.options)
    if cfg.model:
        options.setdefault("model", cfg.model)
    return provider_cls(**options)


register("mock", MockProvider)
register("api", APIProvider)
register("openai", APIProvider)
register("openai_compat", OpenAICompatProvider)
register("deepseek", DeepSeekProvider)
register("qwen", QwenProvider)
register("moonshot", MoonshotProvider)
register("glm", GLMProvider)
