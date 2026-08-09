"""Ronin's own MCP server: the error-code table, the approval gate, ``ronin_task``.

The error codes get case-by-case coverage because a wrong one is not a cosmetic
bug: a client that reads ``-32602`` as ``-32603`` will retry a malformed call until
something times out. The gate gets the same treatment because "a mutating tool
exposed over MCP must not silently become un-gated" is a security claim, and a
claim without a test is a hope.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from ronin.core.types import ApprovalDecision, ApprovalRequest, DangerLevel
from ronin.mcp.jsonrpc import (
    APPROVAL_REQUIRED,
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    TOOL_EXECUTION_FAILED,
    Request,
    encode,
    parse_response,
)
from ronin.mcp.server import (
    DEFAULT_EXPOSED_TOOLS,
    McpServer,
    RoninTaskTool,
    render_call,
    serve_stdio,
)
from ronin.mcp.transport import StdioTransport, memory_duplex
from ronin.tools.base import ToolContext
from ronin.tools.registry import build_registry


def local_registry(root: Path) -> Any:
    return build_registry(ToolContext(root=root, env={"PATH": "/usr/bin:/bin"}))


async def scripted_runner(prompt: str) -> str:
    return f"[nested] {prompt} -> done"


async def approve_everything(request: ApprovalRequest) -> ApprovalDecision:
    return ApprovalDecision(approved=True, reason="test")


async def deny_everything(request: ApprovalRequest) -> ApprovalDecision:
    return ApprovalDecision(approved=False, reason="the human said no")


def server(root: Path, **kwargs: Any) -> McpServer:
    return McpServer(local_registry(root), nested_runner=scripted_runner, **kwargs)


async def handshake(instance: McpServer) -> None:
    await instance.handle(Request(method="initialize", params={}, id=0))


# --------------------------------------------------------------------------- #
# Handshake and listing
# --------------------------------------------------------------------------- #


async def test_initialize_answers_with_a_version_and_the_tools_capability(
    tmp_path: Path,
) -> None:
    response = await server(tmp_path).handle(
        Request(method="initialize", params={"protocolVersion": "2024-11-05"}, id=1)
    )
    assert response is not None and response.result is not None
    assert response.result["protocolVersion"] == "2024-11-05"
    assert response.result["capabilities"] == {"tools": {"listChanged": False}}
    assert response.result["serverInfo"]["name"] == "ronin"


async def test_an_unknown_requested_version_gets_ours_rather_than_an_error(
    tmp_path: Path,
) -> None:
    """Erroring would break a client that is merely newer than we are."""
    response = await server(tmp_path).handle(
        Request(method="initialize", params={"protocolVersion": "3000-01-01"}, id=1)
    )
    assert response is not None and response.result is not None
    assert response.result["protocolVersion"] != "3000-01-01"


async def test_a_call_before_the_handshake_is_an_invalid_request(tmp_path: Path) -> None:
    response = await server(tmp_path).handle(Request(method="tools/list", params={}, id=1))
    assert response is not None and response.error is not None
    assert response.error.code == INVALID_REQUEST
    assert "initialize" in response.error.message


async def test_ping_works_before_the_handshake(tmp_path: Path) -> None:
    response = await server(tmp_path).handle(Request(method="ping", params={}, id=1))
    assert response is not None and response.result == {}


async def test_the_initialized_notification_gets_no_answer(tmp_path: Path) -> None:
    """Answering one is a protocol violation some clients disconnect over."""
    instance = server(tmp_path)
    assert await instance.handle(Request(method="notifications/initialized")) is None
    assert instance.initialized


async def test_an_unknown_notification_is_ignored_not_refused(tmp_path: Path) -> None:
    instance = server(tmp_path)
    await handshake(instance)
    assert await instance.handle(Request(method="notifications/invented")) is None


async def test_tools_list_exposes_the_documented_set_plus_ronin_task(tmp_path: Path) -> None:
    instance = server(tmp_path)
    await handshake(instance)
    response = await instance.handle(Request(method="tools/list", params={}, id=1))
    assert response is not None and response.result is not None
    names = [descriptor["name"] for descriptor in response.result["tools"]]
    # No shell was wired into this registry, so bash is a recorded absence.
    assert names == ["read", "grep", "glob", "edit", "ronin_task"]
    assert instance.unavailable_tools == ("bash",)
    assert set(names) <= {*DEFAULT_EXPOSED_TOOLS, "ronin_task"}


async def test_ronin_task_is_absent_without_an_injected_runner(tmp_path: Path) -> None:
    instance = McpServer(local_registry(tmp_path))
    await handshake(instance)
    response = await instance.handle(Request(method="tools/list", params={}, id=1))
    assert response is not None and response.result is not None
    assert "ronin_task" not in [d["name"] for d in response.result["tools"]]


async def test_the_danger_level_and_the_gate_travel_to_the_client(tmp_path: Path) -> None:
    """A client that cannot see the gate cannot honour it."""
    instance = server(tmp_path)
    descriptors = {d["name"]: d for d in instance.tool_descriptors()}
    assert descriptors["read"]["annotations"]["readOnlyHint"] is True
    assert descriptors["read"]["_meta"]["ronin/requiresApproval"] is False
    assert descriptors["edit"]["annotations"]["readOnlyHint"] is False
    assert descriptors["edit"]["_meta"]["ronin/dangerLevel"] == "mutating"
    assert descriptors["edit"]["_meta"]["ronin/requiresApproval"] is True
    assert descriptors["ronin_task"]["annotations"]["destructiveHint"] is True
    assert descriptors["ronin_task"]["_meta"]["ronin/dangerLevel"] == "destructive"
    assert descriptors["ronin_task"]["_meta"]["ronin/approverAttached"] is False


async def test_every_descriptor_carries_a_schema_and_a_description(tmp_path: Path) -> None:
    for descriptor in server(tmp_path).tool_descriptors():
        assert descriptor["description"]
        assert descriptor["inputSchema"]["type"] == "object"


# --------------------------------------------------------------------------- #
# The error-code table
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ("}{ not json", PARSE_ERROR),
        ('{"jsonrpc": "2.0", "id": 1}', INVALID_REQUEST),
        ('{"jsonrpc": "1.0", "method": "ping", "id": 1}', INVALID_REQUEST),
        ('{"jsonrpc": "2.0", "method": "ping", "id": 1, "params": [1]}', INVALID_PARAMS),
        ('{"jsonrpc": "2.0", "method": "tools/frobnicate", "id": 1}', METHOD_NOT_FOUND),
        ('{"jsonrpc": "2.0", "method": "tools/call", "id": 1, "params": {}}', INVALID_PARAMS),
        (
            '{"jsonrpc":"2.0","method":"tools/call","id":1,"params":{"name":42}}',
            INVALID_PARAMS,
        ),
        (
            '{"jsonrpc":"2.0","method":"tools/call","id":1,'
            '"params":{"name":"read","arguments":"nope"}}',
            INVALID_PARAMS,
        ),
        (
            '{"jsonrpc":"2.0","method":"tools/call","id":1,"params":{"name":"nope"}}',
            INVALID_PARAMS,
        ),
    ],
)
async def test_the_full_error_code_table(tmp_path: Path, raw: str, code: int) -> None:
    instance = server(tmp_path)
    await handshake(instance)
    frame = await instance.handle_frame(raw)
    assert frame is not None
    response = parse_response(frame)
    assert response.error is not None
    assert response.error.code == code, response.error.message


async def test_an_unparseable_frame_answers_with_a_null_id(tmp_path: Path) -> None:
    """The one case where JSON-RPC's null id is correct: there is no id to echo."""
    frame = await server(tmp_path).handle_frame("not json")
    assert frame is not None
    assert parse_response(frame).id is None


async def test_a_malformed_notification_gets_no_answer(tmp_path: Path) -> None:
    instance = server(tmp_path)
    await handshake(instance)
    assert await instance.handle(Request(method="tools/call", params={})) is None


async def test_a_tool_that_refuses_uses_the_server_defined_range(tmp_path: Path) -> None:
    """Not -32603: the tool ran and said no, so a retry would fail identically."""
    instance = server(tmp_path)
    await handshake(instance)
    response = await instance.handle(
        Request(
            method="tools/call",
            params={"name": "read", "arguments": {"path": "absent.txt"}},
            id=1,
        )
    )
    assert response is not None and response.error is not None
    assert response.error.code == TOOL_EXECUTION_FAILED
    assert response.error.data == {"tool": "read", "isError": True}


async def test_a_handler_that_explodes_becomes_an_internal_error(tmp_path: Path) -> None:
    """-32603 is reserved for *our* bug, which is the one thing worth a retry."""

    class Exploding(McpServer):
        def tool_descriptors(self) -> list[dict[str, Any]]:
            raise RuntimeError("bug in the descriptor builder")

    instance = Exploding(local_registry(tmp_path))
    await handshake(instance)
    response = await instance.handle(Request(method="tools/list", params={}, id=1))
    assert response is not None and response.error is not None
    assert response.error.code == INTERNAL_ERROR
    assert "bug in the descriptor builder" in response.error.message


async def test_an_oversized_frame_ends_the_connection_with_an_invalid_request(
    tmp_path: Path,
) -> None:
    near, far = memory_duplex()
    task = asyncio.create_task(serve_stdio(server(tmp_path), far, max_frame_bytes=64))
    near.writer.write(b"x" * 500 + b"\n")
    await near.writer.drain()
    frame = await near.reader.readline()
    response = parse_response(frame)
    assert response.error is not None
    assert response.error.code == INVALID_REQUEST
    assert "64-byte limit" in response.error.message
    await asyncio.wait_for(task, timeout=2.0)


# --------------------------------------------------------------------------- #
# The approval gate
# --------------------------------------------------------------------------- #


async def test_a_gated_tool_with_no_approver_is_refused_and_does_not_run(
    tmp_path: Path,
) -> None:
    """The fail-closed default: exposing Ronin over MCP does not un-gate anything."""
    (tmp_path / "a.txt").write_text("original\n", encoding="utf-8")
    instance = server(tmp_path)
    await handshake(instance)
    response = await instance.handle(
        Request(
            method="tools/call",
            params={
                "name": "edit",
                "arguments": {"path": "a.txt", "old_string": "original", "new_string": "changed"},
            },
            id=1,
        )
    )
    assert response is not None and response.error is not None
    assert response.error.code == APPROVAL_REQUIRED
    assert "no human attached" in response.error.message
    assert (tmp_path / "a.txt").read_bytes() == b"original\n"


async def test_a_denied_approval_is_the_same_refusal(tmp_path: Path) -> None:
    instance = server(tmp_path, approver=deny_everything)
    await handshake(instance)
    response = await instance.handle(
        Request(
            method="tools/call",
            params={"name": "ronin_task", "arguments": {"prompt": "do it"}},
            id=1,
        )
    )
    assert response is not None and response.error is not None
    assert response.error.code == APPROVAL_REQUIRED
    assert "the human said no" in response.error.message


async def test_the_approver_is_shown_what_will_actually_run(tmp_path: Path) -> None:
    seen: list[ApprovalRequest] = []

    async def recording(request: ApprovalRequest) -> ApprovalDecision:
        seen.append(request)
        return ApprovalDecision(approved=True)

    instance = server(tmp_path, approver=recording)
    await handshake(instance)
    await instance.handle(
        Request(
            method="tools/call",
            params={"name": "ronin_task", "arguments": {"prompt": "rm -rf /tmp/x"}},
            id=1,
        )
    )
    (request,) = seen
    assert request.name == "ronin_task"
    assert request.danger_level is DangerLevel.DESTRUCTIVE
    assert "rm -rf /tmp/x" in request.rendered


async def test_an_ungated_tool_never_reaches_the_approver(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")
    asked: list[str] = []

    async def recording(request: ApprovalRequest) -> ApprovalDecision:
        asked.append(request.name)
        return ApprovalDecision(approved=True)

    instance = server(tmp_path, approver=recording)
    await handshake(instance)
    response = await instance.handle(
        Request(
            method="tools/call", params={"name": "read", "arguments": {"path": "a.txt"}}, id=1
        )
    )
    assert response is not None and response.result is not None
    assert asked == []


def test_the_rendered_call_is_not_truncated() -> None:
    """An approval prompt that elides the tail approves something unread."""
    rendered = render_call("bash", {"command": "x" * 400})
    assert "x" * 400 in rendered
    assert rendered.startswith("bash ")


# --------------------------------------------------------------------------- #
# ronin_task
# --------------------------------------------------------------------------- #


async def test_ronin_task_runs_the_injected_runner_and_returns_its_summary(
    tmp_path: Path,
) -> None:
    prompts: list[str] = []

    async def runner(prompt: str) -> str:
        prompts.append(prompt)
        return "read 3 files; the retry lives in client.py:88"

    instance = McpServer(
        local_registry(tmp_path), nested_runner=runner, approver=approve_everything
    )
    await handshake(instance)
    response = await instance.handle(
        Request(
            method="tools/call",
            params={"name": "ronin_task", "arguments": {"prompt": "where is the retry?"}},
            id=1,
        )
    )
    assert response is not None and response.result is not None
    assert response.result["isError"] is False
    assert response.result["content"] == [
        {"type": "text", "text": "read 3 files; the retry lives in client.py:88"}
    ]
    assert prompts == ["where is the retry?"]


@pytest.mark.parametrize("arguments", [{}, {"prompt": ""}, {"prompt": "   "}, {"prompt": 7}])
async def test_ronin_task_refuses_a_missing_or_blank_prompt(
    tmp_path: Path, arguments: dict[str, Any]
) -> None:
    instance = server(tmp_path, approver=approve_everything)
    await handshake(instance)
    response = await instance.handle(
        Request(method="tools/call", params={"name": "ronin_task", "arguments": arguments}, id=1)
    )
    assert response is not None and response.error is not None
    assert response.error.code == TOOL_EXECUTION_FAILED
    assert "'prompt' is required" in response.error.message


def test_ronin_task_declares_the_danger_it_actually_has() -> None:
    """It is a superset of every tool the nested loop has, by definition."""
    spec = RoninTaskTool(scripted_runner).spec()
    assert spec.danger_level is DangerLevel.DESTRUCTIVE
    assert spec.requires_approval is True
    assert "WHEN NOT TO USE" in spec.description


# --------------------------------------------------------------------------- #
# serve_stdio
# --------------------------------------------------------------------------- #


async def test_serve_stdio_answers_a_real_client_over_a_real_transport(
    tmp_path: Path,
) -> None:
    """The integration shape: Ronin's server, Ronin's stdio transport, one pipe."""
    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")
    instance = server(tmp_path, approver=approve_everything)
    near, far = memory_duplex()
    task = asyncio.create_task(serve_stdio(instance, far))
    transport = StdioTransport(streams=near, timeout=2.0)
    await transport.start()
    try:
        await transport.send(Request(method="initialize", params={}, id=1))
        listed = await transport.send(Request(method="tools/list", params={}, id=2))
        assert listed.result is not None
        assert "read" in [d["name"] for d in listed.result["tools"]]

        called = await transport.send(
            Request(
                method="tools/call",
                params={"name": "read", "arguments": {"path": "a.txt"}},
                id=3,
            )
        )
        assert called.result is not None
        assert "hello" in called.result["content"][0]["text"]

        task_call = await transport.send(
            Request(
                method="tools/call",
                params={"name": "ronin_task", "arguments": {"prompt": "summarise"}},
                id=4,
            )
        )
        assert task_call.result is not None
        assert task_call.result["content"][0]["text"] == "[nested] summarise -> done"
    finally:
        await transport.close()
        task.cancel()


async def test_serve_stdio_returns_when_the_client_hangs_up(tmp_path: Path) -> None:
    near, far = memory_duplex()
    task = asyncio.create_task(serve_stdio(server(tmp_path), far))
    near.writer.close()
    await asyncio.wait_for(task, timeout=2.0)


async def test_serve_stdio_sends_nothing_for_a_notification(tmp_path: Path) -> None:
    near, far = memory_duplex()
    task = asyncio.create_task(serve_stdio(server(tmp_path), far))
    near.writer.write(encode(Request(method="notifications/initialized")))
    near.writer.write(encode(Request(method="ping", params={}, id=9)))
    await near.writer.drain()
    first = parse_response(await near.reader.readline())
    assert first.id == 9
    near.writer.close()
    await asyncio.wait_for(task, timeout=2.0)


async def test_a_response_carrying_newlines_survives_the_framing(tmp_path: Path) -> None:
    """The whole reason newline-delimited framing is safe for tool output."""
    (tmp_path / "a.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    near, far = memory_duplex()
    task = asyncio.create_task(serve_stdio(server(tmp_path), far))
    near.writer.write(encode(Request(method="initialize", params={}, id=1)))
    near.writer.write(
        encode(
            Request(
                method="tools/call",
                params={"name": "read", "arguments": {"path": "a.txt"}},
                id=2,
            )
        )
    )
    await near.writer.drain()
    assert parse_response(await near.reader.readline()).id == 1
    line = await near.reader.readline()
    assert line.count(b"\n") == 1
    payload = json.loads(line)
    assert "two" in payload["result"]["content"][0]["text"]
    near.writer.close()
    await asyncio.wait_for(task, timeout=2.0)
