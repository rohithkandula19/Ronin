from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ronin_memory import ShortTermMemory


def _resp(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=10, output_tokens=20),
    )


def test_add_turn_and_messages() -> None:
    mem = ShortTermMemory()
    mem.add_turn("user", "hi")
    mem.add_turn("assistant", "hello")
    msgs = mem.messages()
    assert msgs == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]


def test_no_compress_under_threshold() -> None:
    mem = ShortTermMemory(compress_threshold_tokens=10_000)
    for i in range(4):
        mem.add_turn("user", f"msg {i}")
    assert mem.maybe_compress() is False
    assert len(mem.turns) == 4
    assert mem.summary == ""


def test_compress_summarizes_old_turns() -> None:
    mem = ShortTermMemory(
        keep_recent=2,
        compress_threshold_tokens=20,  # will trigger easily
    )
    for i in range(8):
        mem.add_turn("user" if i % 2 == 0 else "assistant", f"long content {i} " * 10)

    fake_client = MagicMock()
    fake_client.messages.create.return_value = _resp("Discussed greetings and weather.")
    with patch("ronin_memory.short_term.anthropic.Anthropic", return_value=fake_client):
        compressed = mem.maybe_compress()

    assert compressed is True
    assert len(mem.turns) == 2  # kept_recent
    assert "weather" in mem.summary

    msgs = mem.messages()
    # Summary injected as the first user/assistant pair, then the recent turns
    assert msgs[0]["role"] == "user"
    assert "Summary" in msgs[0]["content"]
    assert msgs[1]["role"] == "assistant"
    assert len(msgs) == 4  # 2 synthetic + 2 recent
