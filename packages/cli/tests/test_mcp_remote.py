"""Tests for remote (HTTP) MCP transport — response parsing + config."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ronin_cli.mcp_client import add_remote_mcp_server, load_mcp_servers
from ronin_cli.mcp_remote import parse_rpc_response, stringify_content


def test_parse_plain_json_result() -> None:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"tools": [{"name": "x"}]}})
    res = parse_rpc_response("application/json", body)
    assert res["tools"][0]["name"] == "x"


def test_parse_sse_takes_last_data_frame() -> None:
    body = (
        "event: message\n"
        'data: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n'
        "\n"
    )
    res = parse_rpc_response("text/event-stream", body)
    assert res == {"ok": True}


def test_parse_sse_ignores_done_sentinel() -> None:
    body = 'data: {"jsonrpc":"2.0","id":1,"result":{"v":2}}\ndata: [DONE]\n'
    assert parse_rpc_response("text/event-stream", body) == {"v": 2}


def test_parse_rpc_error_raises() -> None:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "no method"}})
    with pytest.raises(RuntimeError, match="no method"):
        parse_rpc_response("application/json", body)


def test_stringify_text_and_error() -> None:
    assert stringify_content({"content": [{"type": "text", "text": "hi"}]}) == "hi"
    assert stringify_content({"content": [{"type": "text", "text": "boom"}],
                              "isError": True}).startswith("ERROR:")
    assert stringify_content({"content": []}) == "(no output)"


def test_add_remote_writes_url_and_headers(tmp_path: Path) -> None:
    add_remote_mcp_server("linear", "https://mcp.linear.app/mcp", tmp_path,
                          headers={"Authorization": "Bearer tok"})
    spec = load_mcp_servers(tmp_path)["linear"]
    assert spec["url"] == "https://mcp.linear.app/mcp"
    assert spec["headers"]["Authorization"] == "Bearer tok"
    assert "command" not in spec
