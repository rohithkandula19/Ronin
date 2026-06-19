"""Tests for ronin's MCP *server* (ronin exposed as a server other clients call).

The pure protocol handlers are fed hand-built JSON-RPC request dicts and the
response shapes are asserted. ``tools/call`` is driven with a FAKE ``run`` that
returns a canned string, so NO real agent runs, NO network is touched, and NO
stdio happens — exactly the read-only-by-construction surface under test.
"""
from __future__ import annotations

from ronin_cli import mcp_server


def _req(method: str, *, id: int | None = 1, params: dict | None = None) -> dict:
    req: dict = {"jsonrpc": "2.0", "method": method}
    if id is not None:
        req["id"] = id
    if params is not None:
        req["params"] = params
    return req


# --- initialize --------------------------------------------------------------
def test_handle_initialize_shape() -> None:
    resp = mcp_server.handle_initialize(_req("initialize", id=7))
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == 7
    res = resp["result"]
    assert res["protocolVersion"] == mcp_server.PROTOCOL_VERSION
    assert "tools" in res["capabilities"]
    assert res["serverInfo"]["name"] == "ronin"


# --- tools/list --------------------------------------------------------------
def test_handle_tools_list_returns_the_three_readonly_tools() -> None:
    resp = mcp_server.handle_tools_list(_req("tools/list", id=2))
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == 2
    tools = resp["result"]["tools"]
    names = [t["name"] for t in tools]
    assert names == ["ronin_ask", "ronin_consensus", "ronin_research"]
    # Every tool has the required MCP fields and a typed input schema.
    for t in tools:
        assert t["description"]
        assert t["inputSchema"]["type"] == "object"
        assert "properties" in t["inputSchema"]
    # No write/edit/shell tool is ever exposed.
    forbidden = ("edit", "write", "shell", "exec", "run_command", "apply", "patch")
    blob = " ".join(names).lower()
    assert not any(bad in blob for bad in forbidden)


def test_tool_input_schemas_are_what_we_expect() -> None:
    by_name = {t["name"]: t for t in mcp_server.TOOLS}
    assert by_name["ronin_ask"]["inputSchema"]["required"] == ["question"]
    consensus_schema = by_name["ronin_consensus"]["inputSchema"]
    assert consensus_schema["required"] == ["task", "models"]
    assert consensus_schema["properties"]["models"]["type"] == "array"
    assert by_name["ronin_research"]["inputSchema"]["required"] == ["question"]


# --- tools/call (with a fake injected runner) --------------------------------
def test_handle_tools_call_dispatches_to_fake_run() -> None:
    seen: dict = {}

    def fake_run(name: str, arguments: dict) -> str:
        seen["name"] = name
        seen["arguments"] = arguments
        return "CANNED ANSWER"

    req = _req(
        "tools/call",
        id=3,
        params={"name": "ronin_ask", "arguments": {"question": "what is 2+2?"}},
    )
    resp = mcp_server.handle_tools_call(req, run=fake_run)

    # the runner was dispatched with the right tool + args
    assert seen["name"] == "ronin_ask"
    assert seen["arguments"] == {"question": "what is 2+2?"}
    # the envelope is a well-formed tools/call result
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == 3
    res = resp["result"]
    assert res["isError"] is False
    assert res["content"] == [{"type": "text", "text": "CANNED ANSWER"}]


def test_handle_tools_call_consensus_passes_args_through() -> None:
    seen: dict = {}

    def fake_run(name: str, arguments: dict) -> str:
        seen["name"] = name
        seen["arguments"] = arguments
        return "SYNTHESIZED"

    req = _req(
        "tools/call",
        id=4,
        params={
            "name": "ronin_consensus",
            "arguments": {"task": "best db?", "models": ["anthropic", "gemini"]},
        },
    )
    resp = mcp_server.handle_tools_call(req, run=fake_run)
    assert seen["name"] == "ronin_consensus"
    assert seen["arguments"]["models"] == ["anthropic", "gemini"]
    assert resp["result"]["content"][0]["text"] == "SYNTHESIZED"
    assert resp["result"]["isError"] is False


def test_handle_tools_call_unknown_tool_is_jsonrpc_error() -> None:
    req = _req(
        "tools/call",
        id=5,
        params={"name": "ronin_delete_everything", "arguments": {}},
    )
    # fake run should never be reached for an unknown tool
    resp = mcp_server.handle_tools_call(req, run=lambda n, a: "nope")
    assert "result" not in resp
    assert resp["error"]["code"] == mcp_server.METHOD_NOT_FOUND
    assert resp["id"] == 5


def test_handle_tools_call_missing_name_is_invalid_params() -> None:
    req = _req("tools/call", id=6, params={"arguments": {}})
    resp = mcp_server.handle_tools_call(req, run=lambda n, a: "nope")
    assert resp["error"]["code"] == mcp_server.INVALID_PARAMS


def test_handle_tools_call_runner_exception_becomes_iserror_result() -> None:
    def boom(name: str, arguments: dict) -> str:
        raise RuntimeError("provider exploded")

    req = _req(
        "tools/call",
        id=8,
        params={"name": "ronin_research", "arguments": {"question": "x"}},
    )
    resp = mcp_server.handle_tools_call(req, run=boom)
    # a tool that RUNS but fails is a result with isError, not a protocol error
    assert "result" in resp
    assert resp["result"]["isError"] is True
    assert "provider exploded" in resp["result"]["content"][0]["text"]


# --- routing -----------------------------------------------------------------
def test_handle_request_routes_each_method() -> None:
    assert "tools" in mcp_server.handle_request(_req("tools/list"))["result"]
    assert "serverInfo" in mcp_server.handle_request(_req("initialize"))["result"]
    call = mcp_server.handle_request(
        _req("tools/call", id=9, params={"name": "ronin_ask",
                                         "arguments": {"question": "hi"}}),
        run=lambda n, a: "ok",
    )
    assert call["result"]["content"][0]["text"] == "ok"


def test_handle_request_notification_gets_no_reply() -> None:
    # a request with no id is a notification → no response per JSON-RPC
    assert mcp_server.handle_request(_req("notifications/initialized", id=None)) is None


def test_handle_request_unknown_method_is_error() -> None:
    resp = mcp_server.handle_request(_req("does/not/exist", id=11))
    assert resp["error"]["code"] == mcp_server.METHOD_NOT_FOUND
