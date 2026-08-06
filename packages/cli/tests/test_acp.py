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

    def runner(prompt: str, cwd: Path, history: list) -> _Result:
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

    assert first[0]["method"] == "session/update"
    assert first[0]["params"]["update"]["content"]["text"] == "Found the relevant implementation."
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
