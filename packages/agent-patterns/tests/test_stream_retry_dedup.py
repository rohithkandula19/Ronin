"""Regression: a rate-limit (or other transient failure) mid-stream must NOT
duplicate the answer.

Live bug (cerebras gpt-oss-120b): when the provider rate-limited partway through
streaming a response and the agent retried, the retry re-streamed the whole
answer from the start while the already-streamed partial was still on screen, so
the user saw the same answer printed once per retry attempt.

The fix: ``OpenAICompatProvider.stream`` resets its per-attempt accumulators on
retry AND emits a ``reset`` StreamEvent before re-streaming, so the consumer can
clear the partial it has shown. These tests drive the real ``stream`` against a
mocked httpx client and assert the rendered output contains the answer exactly
once, and that the assembled ``done`` text is the answer (not the doubled text).
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx

from ronin_agent_patterns import Message, OpenAICompatProvider


ANSWER = "The answer is 42."


class _FakeStream:
    """Mimics the context manager returned by ``httpx.Client.stream``: a status
    code, headers, ``raise_for_status``, and an ``iter_lines`` SSE generator that
    can optionally raise partway through (a dropped/rate-limited connection)."""

    def __init__(self, status: int, lines: list[str], *, raise_mid: bool = False) -> None:
        self.status_code = status
        self.headers = {"retry-after": "0"}
        self._lines = lines
        self._raise_mid = raise_mid

    def __enter__(self) -> "_FakeStream":
        return self

    def __exit__(self, *_a) -> bool:
        return False

    def read(self) -> bytes:
        return b""

    def raise_for_status(self) -> None:
        return None

    def iter_lines(self):
        for line in self._lines:
            yield line
        if self._raise_mid:
            raise httpx.RemoteProtocolError("peer closed connection (rate limited)")


def _delta(text: str) -> str:
    return "data: " + json.dumps({"choices": [{"delta": {"content": text}}]})


def _final() -> str:
    return "data: " + json.dumps({
        "choices": [{"delta": {}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 2},
    })


def _drive(attempts: list[_FakeStream], monkeypatch):
    """Run the real ``stream`` against ``attempts`` (one _FakeStream per HTTP
    attempt). Returns (rendered_text, done_text, reset_count) where rendered_text
    is what a consumer that honours ``reset`` would have on screen at the end."""
    import ronin_agent_patterns.providers.openai_compat as oc
    monkeypatch.setattr(oc.time, "sleep", lambda *_a, **_k: None)  # no real waiting

    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.__exit__.return_value = False
    fake_client.stream.side_effect = attempts

    rendered: list[str] = []
    done_text = None
    resets = 0
    with patch("ronin_agent_patterns.providers.openai_compat.httpx.Client", return_value=fake_client):
        provider = OpenAICompatProvider(model="m", api_key="sk-x")
        for ev in provider.stream(system="s", messages=[Message(role="user", content="hi")], tools=[]):
            if ev.type == "text":
                rendered.append(ev.text)
            elif ev.type == "reset":
                resets += 1
                rendered.clear()  # a real consumer clears the partial it has shown
            elif ev.type == "done":
                done_text = ev.response.text
    return "".join(rendered), done_text, resets


def test_mid_stream_drop_then_retry_renders_answer_once(monkeypatch) -> None:
    """Attempt 1 streams a partial answer then the connection drops (rate-limit);
    attempt 2 streams the full answer. The user must see the answer ONCE."""
    attempt1 = _FakeStream(200, [_delta("The answer "), _delta("is 42.")], raise_mid=True)
    attempt2 = _FakeStream(200, [_delta("The answer "), _delta("is 42."), _final(), "data: [DONE]"])

    rendered, done_text, resets = _drive([attempt1, attempt2], monkeypatch)

    assert rendered.count(ANSWER) == 1, f"answer duplicated: {rendered!r}"
    assert done_text == ANSWER  # assembled text is the answer, not the doubled text
    assert resets == 1          # a reset was emitted so the consumer cleared the partial


def test_status_429_before_body_then_retry_renders_answer_once(monkeypatch) -> None:
    """Attempt 1 returns a 429 status before any body; attempt 2 streams the
    answer. No partial was shown, so the answer appears once and no reset fires."""
    attempt1 = _FakeStream(429, [])
    attempt2 = _FakeStream(200, [_delta("The answer "), _delta("is 42."), _final(), "data: [DONE]"])

    rendered, done_text, resets = _drive([attempt1, attempt2], monkeypatch)

    assert rendered.count(ANSWER) == 1
    assert done_text == ANSWER
    assert resets == 0  # nothing was emitted on the failed attempt, so no reset needed


def test_normal_stream_has_no_reset(monkeypatch) -> None:
    """A first-attempt success still streams token-by-token with no reset events
    (guards against a regression where the dedup path fires on the happy path)."""
    only = _FakeStream(200, [_delta("The answer "), _delta("is 42."), _final(), "data: [DONE]"])

    rendered, done_text, resets = _drive([only], monkeypatch)

    assert rendered == ANSWER
    assert done_text == ANSWER
    assert resets == 0
