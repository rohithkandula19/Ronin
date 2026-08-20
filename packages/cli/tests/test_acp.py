from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ronin_cli.acp import AcpServer


@dataclass
class _Result:
    output: str
    messages: list = field(default_factory=list)


def _request(method: str, params: dict, request_id: int = 1) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def test_acp_requires_initialize_before_sessions(tmp_path: Path) -> None:
    server = AcpServer(root=tmp_path, runner=lambda *_: _Result("unused"))

    response = server.handle(_request("session/new", {"cwd": str(tmp_path)}))[0]

    assert response["error"]["code"] == -32002


def test_acp_runs_read_only_turn_and_preserves_structured_history(tmp_path: Path) -> None:
    observed: dict[str, object] = {}
    prior = [object()]

    def runner(prompt: str, cwd: Path, history: list, mode: str, emit) -> _Result:
        observed.update(prompt=prompt, cwd=cwd, history=history)
        return _Result("Found the relevant implementation.", messages=prior)

    server = AcpServer(root=tmp_path, runner=runner)
    server.handle(_request("initialize", {"protocolVersion": 1}))
    new = server.handle(_request("session/new", {"cwd": str(tmp_path)}, 2))[0]
    session_id = new["result"]["sessionId"]

    first = server.handle(_request("session/prompt", {
        "sessionId": session_id,
        "prompt": [{"type": "text", "text": "Explain the retry path."}],
    }, 3))
    second = server.handle(_request("session/prompt", {
        "sessionId": session_id,
        "prompt": [{"type": "text", "text": "What should change?"}],
    }, 4))

    assert first[0]["method"] == "ronin/activity"
    assert first[1]["method"] == "session/update"
    assert first[1]["params"]["update"]["content"]["text"] == "Found the relevant implementation."
    assert first[-1]["result"] == {"stopReason": "end_turn"}
    assert observed["prompt"] == "What should change?"
    assert observed["cwd"] == tmp_path.resolve()
    assert observed["history"] == prior
    assert second[-1]["result"]["stopReason"] == "end_turn"


def test_acp_rejects_workspace_escape_and_editor_supplied_mcp(tmp_path: Path) -> None:
    server = AcpServer(root=tmp_path, runner=lambda *_: _Result("unused"))
    server.handle(_request("initialize", {"protocolVersion": 1}))

    escaped = server.handle(_request("session/new", {"cwd": str(tmp_path.parent)}, 2))[0]
    mcp = server.handle(_request("session/new", {
        "cwd": str(tmp_path),
        "mcpServers": [{"name": "untrusted", "command": "anything"}],
    }, 3))[0]

    assert escaped["error"]["code"] == -32602
    assert "trusted root" in escaped["error"]["message"]
    assert mcp["error"]["code"] == -32602
    assert "MCP" in mcp["error"]["message"]


def test_acp_loads_saved_session_and_records_cancellation(tmp_path: Path) -> None:
    calls: list[str] = []

    def runner(prompt: str, *_args) -> _Result:
        calls.append(prompt)
        return _Result("done")

    first = AcpServer(root=tmp_path, runner=runner)
    first.handle(_request("initialize", {"protocolVersion": 1}))
    created = first.handle(_request("session/new", {
        "cwd": str(tmp_path), "roninMode": "proposal",
    }, 2))[0]["result"]["sessionId"]
    first.handle(_request("session/cancel", {"sessionId": created}, 3))

    second = AcpServer(root=tmp_path, runner=runner)
    second.handle(_request("initialize", {"protocolVersion": 1}))
    loaded = second.handle(_request("session/load", {"sessionId": created}, 4))[0]
    stopped = second.handle(_request("session/prompt", {
        "sessionId": created, "prompt": [{"type": "text", "text": "do not run"}],
    }, 5))[0]

    assert loaded["result"]["sessionId"] == created
    assert second.sessions[created].mode == "proposal"
    assert stopped["result"]["stopReason"] == "cancelled"
    assert calls == []


def test_acp_emits_activity_and_streamed_messages(tmp_path: Path) -> None:
    def runner(_prompt: str, _cwd: Path, _history: list, _mode: str, emit) -> _Result:
        emit("first ")
        emit("second")
        return _Result("first second")

    server = AcpServer(root=tmp_path, runner=runner)
    server.handle(_request("initialize", {"protocolVersion": 1}))
    session_id = server.handle(_request("session/new", {"cwd": str(tmp_path)}, 2))[0]["result"]["sessionId"]
    responses = server.handle(_request("session/prompt", {
        "sessionId": session_id, "prompt": [{"type": "text", "text": "go"}],
    }, 3))

    assert responses[0]["method"] == "ronin/activity"
    assert [item["params"]["update"]["content"]["text"] for item in responses if item.get("method") == "session/update"] == ["first ", "second"]
    assert responses[-2]["params"]["state"] == "completed"
