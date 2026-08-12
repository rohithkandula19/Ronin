"""``ronin acp``: an editor drives a Ronin session over stdio, offline.

The claim this file checks is the one the ACP adapter exists to make: a program that
speaks the Agent Client Protocol can open a session on a directory, send a prompt, and
watch the agent's message and tool calls stream back as ``session/update``
notifications — with a real assembled :class:`~ronin.cli.sdk.Agent` at the far end and
a fake only at the provider seam, because the wiring is what is under test and a faked
wiring proves nothing.

The end-to-end tests run a scripted-model :class:`~ronin.cli.acp.AcpServer` under
:func:`~ronin.cli.acp.serve_acp` in a task, and drive it with a minimal inline client
over :func:`~ronin.mcp.transport.memory_duplex` — the same shape
``test_ronin_cli_serve.py`` uses to drive the MCP server. No socket, no subprocess, no
network.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import stream_harness as h

from ronin.cli.acp import (
    PROTOCOL_VERSION,
    AcpServer,
    AgentFactory,
    event_to_update,
    serve_acp,
    stop_reason_to_acp,
)
from ronin.cli.sdk import Agent
from ronin.core.types import (
    Error,
    Event,
    Mode,
    StreamReset,
    TextDelta,
    ToolEnd,
    ToolResult,
    ToolStart,
    TurnEnd,
    TurnStart,
    TurnState,
)
from ronin.mcp.jsonrpc import (
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    Request,
    encode,
)
from ronin.mcp.transport import StreamPair, memory_duplex, read_frame


def workspace(root: Path) -> Path:
    """A minimal but real project for a served session to act on."""
    (root / "notes.md").write_text("the answer lives in notes.md\n", encoding="utf-8", newline="\n")
    return root


def factory_for(
    root: Path, responses: Sequence[Any], *, mode: Mode = Mode.FULL
) -> tuple[AgentFactory, list[Agent]]:
    """An ``open_agent`` factory over a scripted provider, plus the agents it opens.

    The returned list is how a test reaches an Agent the server created for itself —
    the only handle onto the runtime a ``session/cancel`` is supposed to reach.
    """
    created: list[Agent] = []

    async def open_agent(cwd: str) -> Agent:
        router, _provider = h.scripted_router(responses)
        agent = await Agent.open(
            cwd,
            router=router,
            mode=mode,
            home=Path(cwd) / "home",
            environ={},
            record=False,
            connect_mcp=False,
        )
        created.append(agent)
        return agent

    return open_agent, created


# --------------------------------------------------------------------------- #
# The whole path: a minimal ACP client over a pipe
# --------------------------------------------------------------------------- #


async def _send(streams: StreamPair, request_id: int, method: str, params: dict[str, Any]) -> None:
    streams.writer.write(encode(Request(method=method, params=params, id=request_id)))
    await streams.writer.drain()


async def _read_reply(
    streams: StreamPair, request_id: int
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Frames until the response to ``request_id``, collecting notifications on the way.

    A ``session/update`` is a notification (a ``method`` frame with no id) and is kept;
    the response carries ``result`` or ``error`` and ends the read.
    """
    updates: list[dict[str, Any]] = []
    while True:
        payload: dict[str, Any] = json.loads(await read_frame(streams.reader))
        if payload.get("id") == request_id and ("result" in payload or "error" in payload):
            return payload, updates
        if "method" in payload:
            updates.append(payload)


async def _call(
    streams: StreamPair,
    request_id: int,
    method: str,
    params: dict[str, Any],
    *,
    timeout: float = 5.0,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """One request out, its response (and any updates that preceded it) back."""
    await _send(streams, request_id, method, params)
    return await asyncio.wait_for(_read_reply(streams, request_id), timeout=timeout)


def _updates_of(updates: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    """The ``update`` payloads of one ``sessionUpdate`` kind."""
    return [
        u["params"]["update"] for u in updates if u["params"]["update"]["sessionUpdate"] == kind
    ]


async def test_an_acp_client_drives_a_session_end_to_end(tmp_path: Path) -> None:
    """The spec's required integration test: initialize, session/new, session/prompt.

    A scripted model says a known line; the client must receive it inside an
    ``agent_message_chunk`` update during the prompt, and a ``session/prompt`` response
    of ``end_turn`` at the end.
    """
    open_agent, _created = factory_for(workspace(tmp_path), [h.provider_says("the answer is 42.")])
    server = AcpServer(open_agent=open_agent, version="test")
    near, far = memory_duplex()
    serving = asyncio.create_task(serve_acp(server, far))
    try:
        init, _ = await _call(
            near, 1, "initialize", {"protocolVersion": PROTOCOL_VERSION, "clientCapabilities": {}}
        )
        assert init["result"]["protocolVersion"] == PROTOCOL_VERSION
        assert init["result"]["agentCapabilities"]["loadSession"] is False
        assert init["result"]["authMethods"] == []

        new, _ = await _call(near, 2, "session/new", {"cwd": str(tmp_path), "mcpServers": []})
        session_id = new["result"]["sessionId"]
        assert session_id

        prompt, updates = await _call(
            near,
            3,
            "session/prompt",
            {"sessionId": session_id, "prompt": [{"type": "text", "text": "what is the answer?"}]},
        )
        assert prompt["result"]["stopReason"] == "end_turn"

        chunks = _updates_of(updates, "agent_message_chunk")
        assert chunks, f"no agent_message_chunk in {updates}"
        text = "".join(chunk["content"]["text"] for chunk in chunks)
        assert "the answer is 42." in text
        # Every update named the session it belonged to.
        assert all(u["params"]["sessionId"] == session_id for u in updates)
    finally:
        near.writer.close()
        await asyncio.wait_for(serving, timeout=5.0)


async def test_a_prompt_with_a_tool_call_emits_tool_call_updates(tmp_path: Path) -> None:
    """A run that calls a tool surfaces a ``tool_call`` and a ``tool_call_update``.

    The scripted model calls ``read`` on a file that exists, then answers in prose. The
    real read tool runs, so the update pair carries a real id and a completed status.
    """
    responses = [h.provider_calls("read", {"path": "notes.md"}), h.provider_says("read it.")]
    open_agent, _created = factory_for(workspace(tmp_path), responses)
    server = AcpServer(open_agent=open_agent, version="test")
    near, far = memory_duplex()
    serving = asyncio.create_task(serve_acp(server, far))
    try:
        await _call(near, 1, "initialize", {"protocolVersion": PROTOCOL_VERSION})
        new, _ = await _call(near, 2, "session/new", {"cwd": str(tmp_path)})
        session_id = new["result"]["sessionId"]

        prompt, updates = await _call(
            near,
            3,
            "session/prompt",
            {"sessionId": session_id, "prompt": [{"type": "text", "text": "read the notes"}]},
        )
        assert prompt["result"]["stopReason"] == "end_turn"

        starts = _updates_of(updates, "tool_call")
        ends = _updates_of(updates, "tool_call_update")
        assert starts, f"no tool_call in {updates}"
        assert ends, f"no tool_call_update in {updates}"
        assert starts[0]["title"] == "read"
        assert starts[0]["status"] == "pending"
        assert starts[0]["toolCallId"]
        assert ends[0]["status"] == "completed"
        # The id pairs the start with its update, so a client can update the right line.
        assert starts[0]["toolCallId"] == ends[0]["toolCallId"]
    finally:
        near.writer.close()
        await asyncio.wait_for(serving, timeout=5.0)


# --------------------------------------------------------------------------- #
# The dispatch table, tested directly
# --------------------------------------------------------------------------- #


def _bare_server() -> AcpServer:
    """A server whose factory must never be called — for the pre-session error paths."""

    async def never(cwd: str) -> Agent:
        raise AssertionError(f"the agent factory must not be called here: {cwd!r}")

    return AcpServer(open_agent=never)


async def _initialize(server: AcpServer) -> None:
    await server.handle(
        Request(method="initialize", params={"protocolVersion": PROTOCOL_VERSION}, id=0)
    )


async def test_a_method_before_initialize_is_invalid_request(tmp_path: Path) -> None:
    """Anything before the handshake is refused, and the refusal names it."""
    server = _bare_server()
    response = await server.handle(
        Request(method="session/new", params={"cwd": str(tmp_path)}, id=1)
    )
    assert response is not None and response.error is not None
    assert response.error.code == INVALID_REQUEST
    assert "initialize" in response.error.message


async def test_an_unknown_method_is_method_not_found() -> None:
    server = _bare_server()
    await _initialize(server)
    response = await server.handle(Request(method="session/bogus", params={}, id=1))
    assert response is not None and response.error is not None
    assert response.error.code == METHOD_NOT_FOUND


async def test_a_prompt_for_an_unknown_session_is_invalid_params() -> None:
    """The named-but-absent session is the caller's bug, never worth a retry."""
    server = _bare_server()
    await _initialize(server)
    response = await server.handle(
        Request(
            method="session/prompt",
            params={"sessionId": "nope", "prompt": [{"type": "text", "text": "hi"}]},
            id=1,
        )
    )
    assert response is not None and response.error is not None
    assert response.error.code == INVALID_PARAMS
    assert "nope" in response.error.message


async def test_initialize_echoes_a_version_and_advertises_text_only(tmp_path: Path) -> None:
    server = _bare_server()
    response = await server.handle(
        Request(method="initialize", params={"protocolVersion": PROTOCOL_VERSION}, id=1)
    )
    assert response is not None and response.result is not None
    assert response.result["protocolVersion"] == PROTOCOL_VERSION
    caps = response.result["agentCapabilities"]
    assert caps["promptCapabilities"]["image"] is False
    assert caps["loadSession"] is False
    assert server.initialized


async def test_session_cancel_cancels_the_running_turn(tmp_path: Path) -> None:
    """``session/cancel`` is a notification wired to the runtime's cancel.

    Asserted on the policy directly rather than over a racy in-flight turn: the wiring
    is the property, and ``PolicyEngine.cancelled()`` becoming true is exactly it.
    """
    open_agent, created = factory_for(workspace(tmp_path), [h.provider_says("ok.")])
    server = AcpServer(open_agent=open_agent)
    try:
        await _initialize(server)
        new = await server.handle(
            Request(method="session/new", params={"cwd": str(tmp_path)}, id=1)
        )
        assert new is not None and new.result is not None
        session_id = new.result["sessionId"]
        agent = created[0]
        assert not agent.runtime.policy.cancelled()

        # A notification: no response comes back, and the turn is asked to stop.
        cancelled = await server.handle(
            Request(method="session/cancel", params={"sessionId": session_id})
        )
        assert cancelled is None
        assert agent.runtime.policy.cancelled()

        # A cancel for a session we do not have is a silent no-op, not an error.
        ghost = await server.handle(Request(method="session/cancel", params={"sessionId": "ghost"}))
        assert ghost is None
    finally:
        await server.aclose()


async def test_session_new_generates_distinct_opaque_ids(tmp_path: Path) -> None:
    """Two sessions get two ids, derived from a counter rather than time or randomness."""
    open_agent, _created = factory_for(
        workspace(tmp_path), [h.provider_says("a"), h.provider_says("b")]
    )
    server = AcpServer(open_agent=open_agent)
    try:
        await _initialize(server)
        first = await server.handle(
            Request(method="session/new", params={"cwd": str(tmp_path)}, id=1)
        )
        second = await server.handle(
            Request(method="session/new", params={"cwd": str(tmp_path)}, id=2)
        )
        assert first is not None and first.result is not None
        assert second is not None and second.result is not None
        assert first.result["sessionId"] != second.result["sessionId"]
    finally:
        await server.aclose()


async def test_session_new_needs_a_cwd() -> None:
    server = _bare_server()
    await _initialize(server)
    response = await server.handle(Request(method="session/new", params={}, id=1))
    assert response is not None and response.error is not None
    assert response.error.code == INVALID_PARAMS


# --------------------------------------------------------------------------- #
# The pure translation helpers
# --------------------------------------------------------------------------- #


def _update(event: Event) -> dict[str, Any]:
    update = event_to_update(event)
    assert update is not None, f"{type(event).__name__} produced no update"
    return update


def test_event_to_update_maps_a_message_and_a_thought() -> None:
    message = _update(TextDelta(text="hello"))
    assert message["sessionUpdate"] == "agent_message_chunk"
    assert message["content"] == {"type": "text", "text": "hello"}

    thought = _update(TextDelta(text="hmm", thinking=True))
    assert thought["sessionUpdate"] == "agent_thought_chunk"
    assert thought["content"]["text"] == "hmm"


def test_event_to_update_maps_a_tool_call_and_its_outcome() -> None:
    start = _update(ToolStart(tool_use_id="t1", name="read", arguments={"path": "x.py"}))
    assert start["sessionUpdate"] == "tool_call"
    assert start["toolCallId"] == "t1"
    assert start["title"] == "read"
    assert start["status"] == "pending"
    assert start["rawInput"] == {"path": "x.py"}

    ok = _update(ToolEnd(tool_use_id="t1", name="read", result=ToolResult(ok=True, content="body")))
    assert ok["sessionUpdate"] == "tool_call_update"
    assert ok["toolCallId"] == "t1"
    assert ok["status"] == "completed"
    assert ok["content"][0]["content"]["text"] == "body"

    failed = _update(
        ToolEnd(tool_use_id="t2", name="bash", result=ToolResult(ok=False, error="boom"))
    )
    assert failed["status"] == "failed"
    assert failed["content"][0]["content"]["text"] == "boom"


def test_event_to_update_carries_an_error_as_prose() -> None:
    update = _update(Error(message="the model stream ended", kind="protocol"))
    assert update["sessionUpdate"] == "agent_message_chunk"
    assert "the model stream ended" in update["content"]["text"]
    assert "protocol" in update["content"]["text"]


def test_event_to_update_skips_the_lifecycle_events() -> None:
    """TurnEnd drives the final stopReason; it and the other lifecycle events push nothing."""
    assert event_to_update(TurnStart(turn_index=0)) is None
    assert event_to_update(StreamReset()) is None
    assert (
        event_to_update(TurnEnd(turn_index=0, state=TurnState.DONE, stop_reason="no_tool_calls"))
        is None
    )


def test_stop_reason_maps_to_acp_vocabulary() -> None:
    assert stop_reason_to_acp("no_tool_calls") == "end_turn"
    assert stop_reason_to_acp("interrupted") == "cancelled"
    assert stop_reason_to_acp("token_budget") == "max_tokens"
    assert stop_reason_to_acp("cost_budget") == "max_tokens"
    assert stop_reason_to_acp("max_iterations") == "max_tokens"
    assert stop_reason_to_acp("stalled") == "refusal"
    # An empty or unfamiliar reason is a clean end rather than an invented one.
    assert stop_reason_to_acp("") == "end_turn"
    assert stop_reason_to_acp("something_future") == "end_turn"
