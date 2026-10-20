"""OpenAI-compatible HTTP provider.

Most hosted LLM APIs (DeepSeek, OpenAI, Qwen compatible-mode, ...) speak the
same chat-completions protocol. This single class covers them; specific vendors
only override defaults via a small subclass.

API keys are never committed to the repository. They are read from the
CHRIS_API_KEY environment variable (or DEEPSEEK_API_KEY for DeepSeekProvider)
unless explicitly passed in config.
"""

from __future__ import annotations

import json
import os
from typing import Any, Iterator, Sequence

import requests

from chris.llm.base import LLMProvider, LLMResponse, Message

_ENV_KEY_HINT = "CHRIS_API_KEY"


class OpenAICompatProvider(LLMProvider):
    name = "openai_compat"

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        api_key_env: str = _ENV_KEY_HINT,
        model: str = "gpt-4o-mini",
        timeout: float = 30.0,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.api_key = api_key or os.environ.get(api_key_env) or os.environ.get(_ENV_KEY_HINT)
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens

    def _chat_endpoint(self) -> str:
        return f"{self.base_url}/chat/completions"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _payload(
        self, messages: Sequence[Message], stream: bool, **kwargs: Any
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [dict(m) for m in messages],
            "stream": stream,
        }
        if "max_tokens" in kwargs:
            payload["max_tokens"] = kwargs["max_tokens"]
        elif not stream:
            payload["max_tokens"] = self.max_tokens
        for key in ("temperature", "top_p"):
            if key in kwargs:
                payload[key] = kwargs[key]
        return payload

    def generate(self, messages: Sequence[Message], **kwargs: Any) -> str:
        self._require_key()
        for _attempt in range(2):
            response = requests.post(
                self._chat_endpoint(),
                headers=self._headers(),
                json=self._payload(messages, stream=False, **kwargs),
                timeout=self.timeout,
            )
            self._check_response(response)
            data = response.json()
            content = data["choices"][0]["message"].get("content")
            if content and content.strip():
                return content
        raise RuntimeError(
            "LLM returned empty content after retry (thinking model produced no answer)"
        )

    def stream(self, messages: Sequence[Message], **kwargs: Any) -> Iterator[str]:
        self._require_key()
        payload = self._payload(messages, stream=True, **kwargs)
        with requests.post(
            self._chat_endpoint(),
            headers=self._headers(),
            json=payload,
            stream=True,
            timeout=self.timeout,
        ) as response:
            self._check_response(response)
            for line in response.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data:"):
                    continue
                chunk = line[len("data:"):].strip()
                if chunk == "[DONE]":
                    break
                data = json.loads(chunk)
                delta = data["choices"][0].get("delta", {}).get("content")
                if delta:
                    yield delta

    def generate_with_tools(
        self,
        messages: Sequence[Message],
        tools: Sequence[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        self._require_key()
        payload = self._payload(messages, stream=False, **kwargs)
        if tools:
            payload["tools"] = list(tools)
        for _attempt in range(2):
            response = requests.post(
                self._chat_endpoint(),
                headers=self._headers(),
                json=payload,
                timeout=self.timeout,
            )
            self._check_response(response)
            data = response.json()
            message = data["choices"][0].get("message", {})
            content = message.get("content") or ""
            tool_calls = self._parse_tool_calls(message.get("tool_calls"))
            if content.strip() or tool_calls:
                return LLMResponse(content=content, tool_calls=tool_calls)
        raise RuntimeError(
            "LLM returned empty content after retry (thinking model produced no answer)"
        )

    @staticmethod
    def _parse_tool_calls(raw: object) -> list[dict[str, Any]]:
        """Normalize vendor tool_calls into {id, name, arguments} dicts.

        OpenAI-compatible responses use ``{id, type, function:{name, arguments}}``
        where ``arguments`` is a JSON string. Keeps the arguments as the parsed
        dict when possible, falling back to the raw string.
        """
        calls: list[dict[str, Any]] = []
        for entry in raw or []:
            if not isinstance(entry, dict):
                continue
            function = entry.get("function") or {}
            calls.append(
                {
                    "id": entry.get("id") or "",
                    "name": function.get("name") or "",
                    "arguments": function.get("arguments") or {},
                }
            )
        return calls

    def health_check(self) -> bool:
        return self.health_report()["ok"]

    def health_report(self) -> dict[str, Any]:
        self._require_key()
        try:
            response = requests.get(
                f"{self.base_url}/models", headers=self._headers(), timeout=self.timeout
            )
            error = None if response.ok else (response.text[:200] or f"HTTP {response.status_code}")
            return {
                "ok": response.ok,
                "base_url": self.base_url,
                "model": self.model,
                "status": response.status_code,
                "error": error,
            }
        except requests.RequestException as exc:
            return {
                "ok": False,
                "base_url": self.base_url,
                "model": self.model,
                "status": None,
                "error": str(exc),
            }

    def _require_key(self) -> None:
        if not self.api_key:
            raise ValueError(
                f"{type(self).__name__} needs an API key. Pass api_key in config "
                f"or set the {self.api_key_env} environment variable."
            )

    @staticmethod
    def _check_response(response: requests.Response) -> None:
        if response.ok:
            return
        detail = response.text.strip()[:300] or f"HTTP {response.status_code}"
        raise RuntimeError(f"LLM API error {response.status_code}: {detail}")


class APIProvider(OpenAICompatProvider):
    """Generic OpenAI-compatible chat provider selected by ``provider: api``."""

    name = "api"

    def __init__(
        self,
        base_url: str | None = None,
        model: str = "gpt-4o-mini",
        api_key_env: str = _ENV_KEY_HINT,
        **kwargs: Any,
    ) -> None:
        if not base_url:
            raise ValueError(
                "LLM provider 'api' requires options.base_url, for example "
                "https://api.openai.com/v1"
            )
        super().__init__(
            base_url=base_url,
            model=model,
            api_key_env=api_key_env,
            **kwargs,
        )


def _env_key(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


class DeepSeekProvider(OpenAICompatProvider):
    name = "deepseek"

    DEFAULT_BASE_URL = "https://api.deepseek.com"
    DEFAULT_MODEL = "deepseek-v4-flash"

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        **kwargs: Any,
    ) -> None:
        api_key = api_key or _env_key("DEEPSEEK_API_KEY", _ENV_KEY_HINT)
        super().__init__(
            base_url=base_url or self.DEFAULT_BASE_URL,
            api_key=api_key,
            model=model or self.DEFAULT_MODEL,
            **kwargs,
        )


class QwenProvider(OpenAICompatProvider):
    """阿里云百炼 (DashScope) OpenAI 兼容端点。"""

    name = "qwen"

    DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    DEFAULT_MODEL = "qwen-plus"

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        **kwargs: Any,
    ) -> None:
        api_key = api_key or _env_key("DASHSCOPE_API_KEY", _ENV_KEY_HINT)
        super().__init__(
            base_url=base_url or self.DEFAULT_BASE_URL,
            api_key=api_key,
            model=model or self.DEFAULT_MODEL,
            **kwargs,
        )


class MoonshotProvider(OpenAICompatProvider):
    """Kimi (Moonshot AI) OpenAI 兼容端点。"""

    name = "moonshot"

    DEFAULT_BASE_URL = "https://api.moonshot.cn/v1"
    DEFAULT_MODEL = "moonshot-v1-8k"

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        **kwargs: Any,
    ) -> None:
        api_key = api_key or _env_key("MOONSHOT_API_KEY", _ENV_KEY_HINT)
        super().__init__(
            base_url=base_url or self.DEFAULT_BASE_URL,
            api_key=api_key,
            model=model or self.DEFAULT_MODEL,
            **kwargs,
        )


class GLMProvider(OpenAICompatProvider):
    """智谱 GLM OpenAI 兼容端点。"""

    name = "glm"

    DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
    DEFAULT_MODEL = "glm-4-flash"

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        **kwargs: Any,
    ) -> None:
        api_key = api_key or _env_key("ZHIPU_API_KEY", _ENV_KEY_HINT)
        super().__init__(
            base_url=base_url or self.DEFAULT_BASE_URL,
            api_key=api_key,
            model=model or self.DEFAULT_MODEL,
            **kwargs,
        )
