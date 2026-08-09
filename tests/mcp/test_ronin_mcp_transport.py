"""Framing, on all three transports, including the boundaries that break readers.

The one-byte-at-a-time SSE test is the important one here. Ronin's provider layer
shipped real reassembly bugs that only a pathological chunking exposes, and the MCP
transports repeat the same shape, so they get the same treatment.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from mcp_harness import FailingSender, ScriptedSender, sse_bytes

from ronin.mcp.jsonrpc import (
    INVALID_PARAMS,
    JsonRpcError,
    ProtocolError,
    Request,
    Response,
    TransportClosed,
    encode,
)
from ronin.mcp.transport import (
    HttpTransport,
    SseFrame,
    SseFrameReader,
    SseTransport,
    StdioTransport,
    StreamPair,
    TransportTimeout,
    frames_of,
    join_url,
    memory_duplex,
    read_frame,
)

RESPONSE = Response(id=1, result={"ok": True, "text": "line one\nline two"})
PING = Request(method="ping", params={}, id=1)


# --------------------------------------------------------------------------- #
# stdio
# --------------------------------------------------------------------------- #


async def _echo_once(far: StreamPair, reply: bytes) -> None:
    """Read one frame from the server end, then write ``reply`` verbatim."""
    await read_frame(far.reader)
    far.writer.write(reply)
    await far.writer.drain()


async def test_stdio_round_trips_a_request() -> None:
    near, far = memory_duplex()
    transport = StdioTransport(streams=near, timeout=2.0)
    await transport.start()
    task = asyncio.create_task(_echo_once(far, encode(RESPONSE)))
    response = await transport.send(PING)
    await task
    assert response == RESPONSE
    assert response.result is not None
    assert response.result["text"] == "line one\nline two"


async def test_stdio_skips_a_server_initiated_notification_before_the_response() -> None:
    """Servers log and report progress on the same pipe; those are not answers."""
    near, far = memory_duplex()
    transport = StdioTransport(streams=near, timeout=2.0)
    await transport.start()
    noise = encode(Request(method="notifications/message", params={"level": "info"}))
    task = asyncio.create_task(_echo_once(far, noise + encode(RESPONSE)))
    assert await transport.send(PING) == RESPONSE
    await task


async def test_stdio_skips_a_response_to_a_different_id() -> None:
    near, far = memory_duplex()
    transport = StdioTransport(streams=near, timeout=2.0)
    await transport.start()
    stale = encode(Response(id=99, result={"stale": True}))
    task = asyncio.create_task(_echo_once(far, stale + encode(RESPONSE)))
    assert await transport.send(PING) == RESPONSE
    await task


async def test_a_dead_stdio_server_raises_transport_closed() -> None:
    near, far = memory_duplex()
    transport = StdioTransport(streams=near, timeout=2.0)
    await transport.start()
    far.writer.close()
    with pytest.raises(TransportClosed):
        await transport.send(PING)
    assert not transport.alive


async def test_a_non_json_line_is_fatal_and_says_what_to_do() -> None:
    """A banner on stdout corrupts the stream; skipping it would hide the cause."""
    near, far = memory_duplex()
    transport = StdioTransport(streams=near, timeout=2.0)
    await transport.start()
    task = asyncio.create_task(_echo_once(far, b"npm WARN using --force\n" + encode(RESPONSE)))
    with pytest.raises(ProtocolError) as caught:
        await transport.send(PING)
    await task
    assert "stderr" in str(caught.value)
    assert "npm WARN" in str(caught.value)
    assert not transport.alive


async def test_an_oversized_frame_is_refused_with_the_limit_named() -> None:
    near, far = memory_duplex()
    transport = StdioTransport(streams=near, timeout=2.0, max_frame_bytes=64)
    await transport.start()
    huge = encode(Response(id=1, result={"text": "x" * 500}))
    task = asyncio.create_task(_echo_once(far, huge))
    with pytest.raises(ProtocolError, match="64-byte limit"):
        await transport.send(PING)
    await task


async def test_a_silent_stdio_server_times_out_rather_than_wedging_the_turn() -> None:
    near, _far = memory_duplex()
    transport = StdioTransport(streams=near, timeout=0.05)
    await transport.start()
    with pytest.raises(TransportTimeout, match=r"within 0\.05s"):
        await transport.send(PING)
    assert not transport.alive


async def test_stdio_refuses_a_notification_through_send_and_the_reverse() -> None:
    """Answering a notification is a protocol violation; the API makes it awkward."""
    near, _far = memory_duplex()
    transport = StdioTransport(streams=near)
    await transport.start()
    with pytest.raises(ValueError, match="use notify"):
        await transport.send(Request(method="notifications/x"))
    with pytest.raises(ValueError, match="use send"):
        await transport.notify(PING)


def test_stdio_needs_either_streams_or_a_command() -> None:
    with pytest.raises(ValueError, match="either streams"):
        StdioTransport()


async def test_stdio_refuses_streams_and_a_command_together() -> None:
    near, _far = memory_duplex()
    with pytest.raises(ValueError, match="not both"):
        StdioTransport(streams=near, command=("x",))


async def test_closing_twice_is_harmless() -> None:
    near, _far = memory_duplex()
    transport = StdioTransport(streams=near)
    await transport.start()
    await transport.close()
    await transport.close()
    assert not transport.alive


# --------------------------------------------------------------------------- #
# SSE reassembly
# --------------------------------------------------------------------------- #

_STREAM = (
    b": keep-alive comment\n"
    b"event: message\n"
    b'data: {"jsonrpc":"2.0","id":1,'
    b'"result":{"n":1}}\n'
    b"\n"
    b"event: message\n"
    b"data: {\n"
    b'data:   "jsonrpc": "2.0",\n'
    b'data:   "id": 2,\n'
    b'data:   "result": {"n": 2}\n'
    b"data: }\n"
    b"\n"
    b"id: 42\n"
    b"retry: 1000\n"
    b"data: [DONE]\n"
    b"\n"
)


@pytest.mark.parametrize("chunk_size", [1, 2, 3, 7, 13, 64, 4096])
def test_sse_frames_reassemble_at_every_chunk_size(chunk_size: int) -> None:
    """One byte at a time is the case that found real bugs in the provider layer."""
    chunks = [_STREAM[i : i + chunk_size] for i in range(0, len(_STREAM), chunk_size)]
    frames = frames_of(chunks)
    assert [frame.event for frame in frames] == ["message", "message", "message"]
    assert json.loads(frames[0].data)["result"] == {"n": 1}
    assert json.loads(frames[1].data)["result"] == {"n": 2}
    assert frames[2].data == "[DONE]"


def test_a_multi_line_data_field_is_joined_with_newlines() -> None:
    """The single-`data:`-line shortcut truncates any pretty-printed payload."""
    frames = frames_of([b"data: a\ndata: b\ndata: c\n\n"])
    assert frames == [SseFrame(event="message", data="a\nb\nc")]


@pytest.mark.parametrize("newline", [b"\n", b"\r\n", b"\r"])
def test_every_line_ending_dispatches_a_frame(newline: bytes) -> None:
    payload = b"data: hello" + newline + newline
    assert frames_of([payload]) == [SseFrame(event="message", data="hello")]


def test_a_carriage_return_split_from_its_newline_is_not_two_frames() -> None:
    """The bug: treating a trailing `\\r` as a terminator dispatches an empty frame,
    and since a blank line dispatches, the payload silently splits in two."""
    reader = SseFrameReader()
    assert reader.feed(b"data: hello\r") == []
    assert reader.feed(b"\n\r\n") == [SseFrame(event="message", data="hello")]


def test_a_chunk_boundary_inside_a_multibyte_character_is_harmless() -> None:
    payload = "data: 日本語 — naïve\n\n".encode()
    frames = frames_of([payload[i : i + 1] for i in range(len(payload))])
    assert frames == [SseFrame(event="message", data="日本語 — naïve")]


def test_a_stream_that_ends_without_a_blank_line_still_yields_its_frame() -> None:
    """Servers close right after the last `data:` often enough to matter."""
    reader = SseFrameReader()
    assert reader.feed(b"data: tail") == []
    assert reader.finish() == [SseFrame(event="message", data="tail")]


def test_unknown_fields_and_comments_are_ignored() -> None:
    frames = frames_of([b": hi\nfoo: bar\nid: 9\ndata: x\n\n"])
    assert frames == [SseFrame(event="message", data="x")]


# --------------------------------------------------------------------------- #
# SSE and HTTP transports
# --------------------------------------------------------------------------- #


async def test_the_sse_transport_returns_the_frame_matching_the_id() -> None:
    """A client taking the *first* frame would return a progress notification."""
    progress = json.dumps({"jsonrpc": "2.0", "method": "notifications/progress"})
    stale = json.dumps({"jsonrpc": "2.0", "id": 99, "result": {"stale": True}})
    wanted = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"n": 1}})
    sender = ScriptedSender([sse_bytes(progress, stale, wanted)], chunk_size=1)
    transport = SseTransport(url="https://x.invalid/mcp", sender=sender)
    response = await transport.send(PING)
    assert response.result == {"n": 1}
    assert sender.posted[0]["method"] == "ping"


async def test_an_sse_stream_that_never_answers_is_an_error_not_a_hang() -> None:
    sender = ScriptedSender([sse_bytes(json.dumps({"jsonrpc": "2.0", "id": 5, "result": {}}))])
    transport = SseTransport(url="https://x.invalid/mcp", sender=sender)
    with pytest.raises(TransportClosed, match="without a response"):
        await transport.send(PING)
    assert not transport.alive


async def test_a_non_json_sse_frame_is_skipped_not_fatal() -> None:
    """Gateways inject keep-alive text; killing a turn over one is self-inflicted."""
    wanted = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"n": 1}})
    sender = ScriptedSender([sse_bytes("still working…", wanted)], chunk_size=1)
    transport = SseTransport(url="https://x.invalid/mcp", sender=sender)
    assert (await transport.send(PING)).result == {"n": 1}


async def test_the_http_transport_round_trips_one_json_body() -> None:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"tools": []}}).encode()
    sender = ScriptedSender([body], chunk_size=1)
    transport = HttpTransport(url="https://x.invalid/mcp", sender=sender)
    await transport.start()
    assert (await transport.send(PING)).result == {"tools": []}


async def test_the_http_transport_surfaces_a_jsonrpc_error_as_a_response() -> None:
    """A transport failure raises; the server saying no is a value."""
    body = encode(Response(id=1, error=JsonRpcError(INVALID_PARAMS, "bad name")))
    transport = HttpTransport(url="https://x.invalid/mcp", sender=ScriptedSender([body]))
    response = await transport.send(PING)
    assert response.error is not None
    assert response.error.code == INVALID_PARAMS
    assert transport.alive


async def test_a_non_response_http_body_kills_the_transport_with_a_reason() -> None:
    transport = HttpTransport(url="https://x.invalid/mcp", sender=ScriptedSender([b"<html>"]))
    with pytest.raises(ProtocolError, match="non-response"):
        await transport.send(PING)
    assert not transport.alive
    assert "https://x.invalid/mcp" in transport.detail


async def test_an_oversized_http_body_is_refused() -> None:
    body = encode(Response(id=1, result={"text": "y" * 4096}))
    transport = HttpTransport(
        url="https://x.invalid/mcp", sender=ScriptedSender([body], chunk_size=16),
        max_frame_bytes=128,
    )
    with pytest.raises(TransportClosed, match="128-byte limit"):
        await transport.send(PING)


async def test_a_sender_that_raises_becomes_transport_closed() -> None:
    transport = HttpTransport(url="https://x.invalid/mcp", sender=FailingSender())
    with pytest.raises(TransportClosed, match="connection reset"):
        await transport.send(PING)
    assert not transport.alive


async def test_a_dead_network_transport_does_not_try_again() -> None:
    """Retrying inside a dead transport is how one outage becomes N timeouts."""
    sender = FailingSender()
    transport = HttpTransport(url="https://x.invalid/mcp", sender=sender)
    for _ in range(3):
        with pytest.raises(TransportClosed, match="connection reset"):
            await transport.send(PING)
    assert sender.calls == 1


def test_the_network_transports_need_a_url() -> None:
    sender = ScriptedSender([b""])
    with pytest.raises(ValueError, match="needs a url"):
        HttpTransport(url="", sender=sender)
    with pytest.raises(ValueError, match="needs a url"):
        SseTransport(url="", sender=sender)


@pytest.mark.parametrize(
    ("base", "path", "expected"),
    [
        ("https://x.invalid", "mcp", "https://x.invalid/mcp"),
        ("https://x.invalid/", "/mcp", "https://x.invalid/mcp"),
        ("https://x.invalid/v1/", "mcp", "https://x.invalid/v1/mcp"),
    ],
)
def test_join_url_tolerates_the_slashes_people_actually_type(
    base: str, path: str, expected: str
) -> None:
    assert join_url(base, path) == expected
