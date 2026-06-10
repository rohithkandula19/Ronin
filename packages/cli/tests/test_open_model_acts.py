"""End-to-end proof: an open model (cerebras-style) that emits tool calls as TEXT
now actually edits files through the full ronin code agent — not 'just chats'."""
from __future__ import annotations

from pathlib import Path

import httpx

from ronin_cli import code_mode
from ronin_cli.config import RoninConfig


class _Resp:
    status_code = 200

    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


class _QueueClient:
    """httpx.Client stand-in returning queued chat-completion payloads in order."""

    def __init__(self, payloads):
        self._payloads = list(payloads)

    def __call__(self, *a, **k):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, *a, **k):
        return _Resp(self._payloads.pop(0))


def _msg(content, finish="stop"):
    return {"choices": [{"message": {"content": content}, "finish_reason": finish}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3}}


def test_open_model_writes_a_file(monkeypatch, tmp_path: Path) -> None:
    # turn 1: the model "chats" a write_file call as text (no structured tool_calls)
    # turn 2: a plain final answer
    payloads = [
        _msg('Sure, creating it.\n<tool_call>{"name": "write_file", '
             '"arguments": {"path": "hello.py", "content": "print(\\"hi\\")\\n"}}</tool_call>'),
        _msg("Done — created hello.py."),
    ]
    monkeypatch.setattr(httpx, "Client", _QueueClient(payloads))

    cfg = RoninConfig(provider="cerebras", provider_keys={"cerebras": "x"})
    res = code_mode.run_code_agent(cfg, "create hello.py", root=tmp_path,
                                   console=None, yolo=True)

    assert res.success
    assert (tmp_path / "hello.py").is_file(), "the agent must actually create the file"
    assert (tmp_path / "hello.py").read_text() == 'print("hi")\n'
