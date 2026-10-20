"""Remote embedding provider - OpenAI-compatible /embeddings endpoint.

Vendor-agnostic: works with any API that speaks the OpenAI embeddings protocol
(OpenAI, Qwen/DashScope compatible-mode, ...). It is NOT tied to a vendor and
is never required for Chris to run - the semantic retriever falls back to
keyword retrieval when this provider fails or has no API key.
"""

from __future__ import annotations

import os
from typing import Any, Sequence

import requests

from chris.embedding.base import EmbeddingProvider

_ENV_KEY_HINT = "CHRIS_API_KEY"


class RemoteEmbeddingProvider(EmbeddingProvider):
    name = "remote"

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        model: str = "text-embedding-3-small",
        dimension: int = 0,
        timeout: float = 30.0,
        **kwargs: Any,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or os.environ.get(_ENV_KEY_HINT)
        self.model = model
        self.dimension = int(dimension) if dimension else 0
        self.timeout = timeout

    def _endpoint(self) -> str:
        return f"{self.base_url}/embeddings"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def embed(self, text: str) -> list[float]:
        vectors = self.embed_batch([text])
        return vectors[0]

    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        self._require_key()
        payload: dict[str, Any] = {"model": self.model, "input": list(texts)}
        response = requests.post(
            self._endpoint(), headers=self._headers(), json=payload, timeout=self.timeout
        )
        if not response.ok:
            detail = response.text.strip()[:300] or f"HTTP {response.status_code}"
            raise RuntimeError(f"Embedding API error {response.status_code}: {detail}")
        data = response.json()
        items = data.get("data", [])
        if not isinstance(items, list) or not items:
            raise RuntimeError("Embedding API returned no vectors")
        vectors: list[list[float]] = []
        for item in sorted(items, key=lambda it: it.get("index", 0)):
            vectors.append(list(item["embedding"]))
        if vectors:
            self.dimension = len(vectors[0])
        return vectors

    def health_check(self) -> bool:
        return self.health_report()["ok"]

    def health_report(self) -> dict:
        try:
            probe = self.embed_batch(["ping"])
            ok = bool(probe) and len(probe[0]) > 0
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "name": self.name, "model": self.model,
                    "dimension": self.dimension, "error": str(exc)}
        return {"ok": ok, "name": self.name, "model": self.model,
                "dimension": self.dimension}

    def _require_key(self) -> None:
        if not self.api_key:
            raise ValueError(
                f"{type(self).__name__} needs an API key. Pass api_key in config "
                f"or set the {_ENV_KEY_HINT} environment variable."
            )
