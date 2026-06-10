"""A provider that fails over across several backends.

Single-vendor agents die when their one provider rate-limits or has an outage.
``FailoverProvider`` wraps an ordered list of providers — primary first — and on
any error continues on the next, so a turn survives a provider going down. This
is something a single-vendor tool structurally can't do.

Semantics:
- ``complete`` tries each provider in order; the first that returns wins. If all
  raise, the last error propagates.
- ``stream`` tries each provider, but only fails over if a provider errors
  *before emitting any text* — once tokens have streamed to the user we can't
  silently restart on another model, so a mid-stream failure propagates.
- ``last_used`` records which provider answered, so the UI can show a failover
  actually happened (e.g. "⚡ failed over to gemini").
"""
from __future__ import annotations

from typing import Iterator

from ..types import Tool
from .base import LLMProvider, LLMResponse, Message, StreamEvent


class FailoverProvider(LLMProvider):
    """Try ``providers`` in order; advance to the next on failure."""

    model: str = "failover"
    providers: list[LLMProvider]
    labels: list[str] = []  # human names parallel to providers, for messages
    last_used: str | None = None
    # Set when a turn fell through to a non-primary provider, so callers can
    # surface it once. Cleared at the start of every call.
    failed_over_to: str | None = None

    def _label(self, i: int) -> str:
        if i < len(self.labels) and self.labels[i]:
            return self.labels[i]
        return getattr(self.providers[i], "model", f"provider-{i}")

    def complete(
        self, *, system: str, messages: list[Message], tools: list[Tool], max_tokens: int = 4096,
    ) -> LLMResponse:
        if not self.providers:
            raise RuntimeError("FailoverProvider has no providers configured")
        self.failed_over_to = None
        last_error: Exception | None = None
        for i, provider in enumerate(self.providers):
            try:
                resp = provider.complete(
                    system=system, messages=messages, tools=tools, max_tokens=max_tokens)
                self.last_used = self._label(i)
                if i > 0:
                    self.failed_over_to = self._label(i)
                return resp
            except Exception as e:  # noqa: BLE001 — failover is the whole point
                last_error = e
        raise RuntimeError(
            f"all {len(self.providers)} providers failed; last error: {last_error}"
        ) from last_error

    def stream(
        self, *, system: str, messages: list[Message], tools: list[Tool], max_tokens: int = 4096,
    ) -> Iterator[StreamEvent]:
        if not self.providers:
            raise RuntimeError("FailoverProvider has no providers configured")
        self.failed_over_to = None
        last_error: Exception | None = None
        for i, provider in enumerate(self.providers):
            emitted = False
            try:
                for ev in provider.stream(
                    system=system, messages=messages, tools=tools, max_tokens=max_tokens):
                    emitted = True
                    yield ev
                self.last_used = self._label(i)
                if i > 0:
                    self.failed_over_to = self._label(i)
                return
            except Exception as e:  # noqa: BLE001
                last_error = e
                if emitted:
                    # tokens already reached the user — can't restart elsewhere
                    raise
                # nothing emitted yet → safe to try the next provider
        raise RuntimeError(
            f"all {len(self.providers)} providers failed; last error: {last_error}"
        ) from last_error
