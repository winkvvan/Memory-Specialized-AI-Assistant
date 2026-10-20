"""Deterministic mock provider for tests and offline development."""

from __future__ import annotations

from typing import Iterator, Sequence

from chris.llm.base import LLMProvider, Message


class MockProvider(LLMProvider):
    name = "mock"

    def __init__(
        self,
        model: str = "mock-default",
        responses: Sequence[str] | None = None,
        default_reply: str = "I am the mock provider.",
        **kwargs: object,
    ) -> None:
        self.model = model
        self._responses = list(responses) if responses else []
        self._default_reply = default_reply
        self._index = 0

    def generate(self, messages: Sequence[Message], **kwargs: object) -> str:
        if self._responses:
            reply = self._responses[self._index % len(self._responses)]
            self._index += 1
            return reply
        last = messages[-1]["content"] if messages else ""
        return f"{self._default_reply} (echo: {last[:40]})"

    def stream(self, messages: Sequence[Message], **kwargs: object) -> Iterator[str]:
        text = self.generate(messages, **kwargs)
        for word in text.split(" "):
            yield word + " "

    def health_check(self) -> bool:
        return True

    def health_report(self) -> dict[str, object]:
        return {
            "ok": True,
            "base_url": "mock",
            "model": self.model,
            "status": 200,
            "error": None,
        }
