"""Regression (end-to-end render): a retried response must render exactly once.

Live bug (cerebras gpt-oss-120b): a rate-limit mid-stream made the agent retry,
the retry re-streamed the whole answer, and the already-streamed partial stayed
on screen, so the user saw the answer printed once per retry attempt.

This drives the REAL agent loop (``ReActAgent.run``) and the REAL terminal
renderer (``LiveRenderer``) with a fake provider that streams a partial answer,
emits a ``reset`` (the signal a retry sends after a mid-stream rate-limit), then
re-streams the full answer. The captured console output must contain the answer
EXACTLY ONCE.
"""
from __future__ import annotations

import io
from typing import Iterator

from rich.console import Console

from ronin_agent_patterns import (
    LLMProvider,
    LLMResponse,
    ReActAgent,
    StreamEvent,
)
from ronin_cli.streaming import LiveRenderer


ANSWER = "The answer is forty-two and that is final."


class _RateLimitMidStreamProvider(LLMProvider):
    """Streams a partial answer, signals a reset (as a real retry would after a
    mid-stream rate-limit), then re-streams the FULL answer and finishes."""

    model: str = "fake-retry"

    def complete(self, **_kw) -> LLMResponse:  # pragma: no cover - streaming path only
        return LLMResponse(text=ANSWER, stop_reason="end_turn")

    def stream(self, **_kw) -> Iterator[StreamEvent]:
        # Attempt 1 streams the WHOLE answer, then the connection is rate-limited
        # before it can finish -> the provider emits a reset and retries. (This is
        # the live bug: the answer was already fully on screen, then re-streamed.)
        for word in ANSWER.split(" "):
            yield StreamEvent(type="text", text=word + " ")
        yield StreamEvent(type="reset")
        # Attempt 2 re-streams the whole answer from the start, then done.
        for word in ANSWER.split(" "):
            yield StreamEvent(type="text", text=word + " ")
        yield StreamEvent(type="done", response=LLMResponse(text=ANSWER, stop_reason="end_turn"))


def _run_and_capture(*, terminal: bool) -> str:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=terminal, width=100)
    renderer = LiveRenderer(console)
    agent = ReActAgent(
        system="be brief",
        tools=[],
        provider=_RateLimitMidStreamProvider(),
        max_iterations=2,
    )
    renderer.start()
    result = agent.run(
        "what is the answer",
        on_text=renderer.on_text,
        on_reset=renderer.on_reset,
    )
    renderer.finish()
    assert result.output == ANSWER  # the agent's final answer is the answer, once
    return buf.getvalue()


def test_retried_response_renders_answer_exactly_once_terminal() -> None:
    """Terminal path (the live bug's environment): assistant text streams into a
    live Markdown block. The first attempt streamed the WHOLE answer before the
    rate-limit; the reset clears that block so the retry's re-stream replaces it
    rather than appending. The answer must render EXACTLY ONCE.

    Without the fix the same answer was printed once per retry attempt.
    """
    out = _run_and_capture(terminal=True)
    assert out.count(ANSWER) == 1, f"answer duplicated in render: {out!r}"
