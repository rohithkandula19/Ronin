"""End-to-end failover behaviour with fake providers (offline, no network).

Asserts the contract that matters in practice:
- a rate-limit error from the primary -> the fallback answers the SAME request.
- no error from the primary -> the fallback is never called.

These drive a FailoverProvider directly (the object build_provider produces for a
Cerebras same-key chain) so the test stays fast and deterministic.
"""
from __future__ import annotations

from typing import Iterator

from ronin_agent_patterns import (
    FailoverProvider,
    FakeProvider,
    LLMResponse,
    Message,
)
from ronin_agent_patterns.providers.base import LLMProvider, StreamEvent


class _RateLimited(LLMProvider):
    """Primary that always 429s — stands in for a rate-limited Cerebras model.
    Records whether it was called so the test can assert call counts."""

    model: str = "primary"
    calls: int = 0

    def complete(self, **_kw) -> LLMResponse:
        self.calls += 1
        raise RuntimeError("429 Too Many Requests: rate limit exceeded")

    def stream(self, **_kw) -> Iterator[StreamEvent]:
        self.calls += 1
        raise RuntimeError("429 Too Many Requests: rate limit exceeded")
        yield  # pragma: no cover — makes this a generator


def _args() -> dict:
    return {"system": "s", "messages": [Message(role="user", content="hi")], "tools": []}


def test_rate_limit_on_primary_uses_fallback() -> None:
    primary = _RateLimited()
    fallback = FakeProvider(model="sibling", responses=[LLMResponse(text="answer from sibling")])
    fo = FailoverProvider(providers=[primary, fallback],
                          labels=["cerebras:gpt-oss-120b", "cerebras:zai-glm-4.7"])

    resp = fo.complete(**_args())

    assert resp.text == "answer from sibling"
    assert primary.calls == 1            # primary was tried
    assert len(fallback.calls) == 1      # and the fallback rescued the SAME request
    assert fo.failed_over_to == "cerebras:zai-glm-4.7"


def test_no_error_never_calls_fallback() -> None:
    primary = FakeProvider(model="primary", responses=[LLMResponse(text="answer from primary")])
    fallback = FakeProvider(model="sibling", responses=[LLMResponse(text="should not be used")])
    fo = FailoverProvider(providers=[primary, fallback],
                          labels=["cerebras:gpt-oss-120b", "cerebras:zai-glm-4.7"])

    resp = fo.complete(**_args())

    assert resp.text == "answer from primary"
    assert len(primary.calls) == 1
    assert len(fallback.calls) == 0      # healthy primary -> fallback untouched
    assert fo.failed_over_to is None
