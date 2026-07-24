"""STATE-1/3: sessions now persist the full structured message list (real
--continue, not a 6-line text tail), write atomically, and warn on a corrupt
file instead of silently loading empty."""
from __future__ import annotations

import json
import pytest

from ronin_agent_patterns import Message, ToolCall
from ronin_cli import sessions


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # sessions live under ./.ronin (cwd) — don't pollute the repo


def test_structured_messages_round_trip():
    msgs = [
        Message(role="user", content="read config.py"),
        Message(role="assistant", content="",
                tool_calls=[ToolCall(id="c1", name="read_file", arguments={"path": "config.py"})]),
        Message(role="tool", content="PORT = 8080", tool_call_id="c1"),
        Message(role="assistant", content="It sets PORT to 8080."),
    ]
    sessions.save_session(".", ["USER: read config.py", "ASSISTANT: It sets PORT to 8080."],
                          session_id="20260101-000001-aaaaaa", messages=msgs)
    restored = sessions.load_session_messages("20260101-000001-aaaaaa")
    assert len(restored) == 4
    assert restored[1].tool_calls[0].name == "read_file"
    assert restored[1].tool_calls[0].arguments == {"path": "config.py"}
    assert restored[2].content == "PORT = 8080" and restored[2].tool_call_id == "c1"


def test_legacy_v1_file_has_no_structured_messages():
    # a v1 file (transcript only) → load_session_messages returns [] (caller falls
    # back to the text tail), load_session still returns the transcript.
    sessions.save_session(".", ["USER: hi", "ASSISTANT: yo"], session_id="20260101-000002-bbbbbb")
    d = sessions._dir()
    data = json.loads((d / "20260101-000002-bbbbbb.json").read_text())
    del data["messages"]; data["version"] = 1
    (d / "20260101-000002-bbbbbb.json").write_text(json.dumps(data))
    assert sessions.load_session_messages("20260101-000002-bbbbbb") == []
    assert sessions.load_session("20260101-000002-bbbbbb") == ["USER: hi", "ASSISTANT: yo"]


def test_save_is_atomic_no_tmp_left():
    sessions.save_session(".", ["USER: q"], session_id="20260101-000003-cccccc")
    d = sessions._dir()
    assert (d / "20260101-000003-cccccc.json").is_file()
    assert not list(d.glob("*.tmp"))  # tmp was renamed, not left behind


def test_corrupt_file_warns_not_silent(capsys):
    sessions.save_session(".", ["USER: q"], session_id="20260101-000004-dddddd")
    d = sessions._dir()
    (d / "20260101-000004-dddddd.json").write_text("{ this is not json")
    result = sessions.load_session("20260101-000004-dddddd")
    assert result == []
    err = capsys.readouterr().err
    assert "unreadable" in err  # explicit warning, not a silent empty
