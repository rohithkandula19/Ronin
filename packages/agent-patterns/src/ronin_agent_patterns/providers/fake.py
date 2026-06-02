"""In-memory provider for tests. Returns a queue of canned ``LLMResponse``s in order."""
from __future__ import annotations

from typing import Iterator

from pydantic import Field, PrivateAttr

from ..types import Tool
from .base import LLMProvider, LLMResponse, Message, StreamEvent


class FakeProvider(LLMProvider):
    """Yields ``responses`` in order on successive ``complete()`` calls.

    Records every call's ``messages`` and ``tools`` for assertions in tests.
    """

    model: str = "fake-model"
    responses: list[LLMResponse] = Field(default_factory=list)

    _calls: list[dict] = PrivateAttr(default_factory=list)
    _index: int = PrivateAttr(default=0)

    @property
    def calls(self) -> list[dict]:
        return self._calls

    def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[Tool],
        max_tokens: int = 4096,
    ) -> LLMResponse:
        self._calls.append({
            "system": system,
            "messages": [m.model_dump() for m in messages],
            "tool_names": [t.name for t in tools],
            "max_tokens": max_tokens,
        })
        if self._index >= len(self.responses):
            raise RuntimeError(
                f"FakeProvider exhausted: {self._index + 1} calls but only "
                f"{len(self.responses)} responses queued"
            )
        response = self.responses[self._index]
        self._index += 1
        return response

    def stream(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[Tool],
        max_tokens: int = 4096,
    ) -> Iterator[StreamEvent]:
        """Simulate token streaming by emitting the response text word-by-word,
        then a final ``done`` event — exercises the agent's delta-forwarding path
        without a network."""
        response = self.complete(
            system=system, messages=messages, tools=tools, max_tokens=max_tokens
        )
        if response.text:
            words = response.text.split(" ")
            for i, w in enumerate(words):
                yield StreamEvent(type="text", text=(w if i == 0 else " " + w))
        yield StreamEvent(type="done", response=response)
