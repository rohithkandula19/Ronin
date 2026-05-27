from __future__ import annotations

import sys
from pathlib import Path

from ro_claude_kit_cli.mcp_client import MCPClient, add_mcp_server, build_mcp_tools

# A minimal MCP server (JSON-RPC over stdio) used as a test fixture.
ECHO_SERVER = r'''
import sys, json
def send(o):
    sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    mid, method = msg.get("id"), msg.get("method")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "serverInfo": {"name": "echo", "version": "1"}}})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": mid, "result": {"tools": [
            {"name": "echo", "description": "echo text back",
             "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}},
                             "required": ["text"]}}]}})
    elif method == "tools/call":
        args = msg["params"]["arguments"]
        send({"jsonrpc": "2.0", "id": mid, "result": {
            "content": [{"type": "text", "text": "echo: " + args.get("text", "")}]}})
'''


def _write_server(tmp_path: Path) -> Path:
    srv = tmp_path / "echo_server.py"
    srv.write_text(ECHO_SERVER)
    return srv


def test_mcp_client_handshake_and_call(tmp_path: Path) -> None:
    srv = _write_server(tmp_path)
    client = MCPClient("echo", sys.executable, [str(srv)])
    try:
        tools = client.start(timeout=15)
        assert any(t["name"] == "echo" for t in tools)
        assert client.call_tool("echo", {"text": "hi"}, timeout=15) == "echo: hi"
    finally:
        client.stop()


def test_build_mcp_tools_from_config(tmp_path: Path) -> None:
    srv = _write_server(tmp_path)
    add_mcp_server("echo", sys.executable, [str(srv)], root=tmp_path)
    tools = build_mcp_tools(tmp_path)
    by_name = {t.name: t for t in tools}
    assert "echo__echo" in by_name              # namespaced by server
    assert by_name["echo__echo"].handler(text="yo") == "echo: yo"


def test_add_then_remove_server(tmp_path: Path) -> None:
    from ro_claude_kit_cli.mcp_client import add_mcp_server, load_mcp_servers, remove_mcp_server
    add_mcp_server("fs", "npx", ["-y", "x", "."], root=tmp_path)
    assert "fs" in load_mcp_servers(tmp_path)
    assert remove_mcp_server("fs", tmp_path) is True
    assert "fs" not in load_mcp_servers(tmp_path)
    assert remove_mcp_server("fs", tmp_path) is False   # already gone
