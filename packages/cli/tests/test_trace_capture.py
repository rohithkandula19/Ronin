"""Trace capture: opt-in, local-only, canonical-shape rows, never breaks a session."""
from __future__ import annotations

import json

from ronin_agent_patterns import Message
from ronin_agent_patterns.providers.base import ToolCall

from ronin_cli.trace_capture import maybe_capture


def _msgs():
    return [
        Message(role="user", content="bump the timeout"),
        Message(role="assistant", tool_calls=[
            ToolCall(id="c1", name="read_file", arguments={"path": "src/x.py"})]),
        Message(role="tool", tool_call_id="c1", content="TIMEOUT = 30"),
        Message(role="assistant", content="Done."),
    ]


def test_off_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("RONIN_CAPTURE_TRACES", raising=False)
    assert maybe_capture(_msgs()) is None
    assert list(tmp_path.iterdir()) == []


def test_opt_in_writes_local_sft_row(monkeypatch, tmp_path):
    monkeypatch.setenv("RONIN_CAPTURE_TRACES", str(tmp_path))
    path = maybe_capture(_msgs(), system="You are Ronin.")
    assert path is not None and path.parent == tmp_path
    row = json.loads(path.read_text().splitlines()[0])
    assert row["family"] == "captured_trace"
    assert row["messages"][0] == {"role": "system", "content": "You are Ronin."}
    call = row["messages"][2]["tool_calls"][0]
    # canonical: OBJECT arguments, OpenAI shape
    assert call["function"]["name"] == "read_file"
    assert call["function"]["arguments"] == {"path": "src/x.py"}


def test_capture_failure_never_raises(monkeypatch, tmp_path):
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("file, not dir")
    monkeypatch.setenv("RONIN_CAPTURE_TRACES", str(blocker))
    assert maybe_capture(_msgs()) is None  # swallowed, session unharmed


def test_no_network_imports():
    # no-telemetry is enforced structurally: the module must not import anything
    # capable of transmission.
    import ronin_cli.trace_capture as tc
    src = open(tc.__file__).read()
    for banned in ("urllib", "requests", "httpx", "socket", "http.client"):
        assert banned not in src
