"""Shared fakes for the MCP tests: an in-process server, and scripted senders.

Named ``mcp_harness`` rather than ``harness`` because the test directories are
deliberately not packages, so every module in ``tests/`` is importable by its bare
basename and two of them cannot share one.

The fakes sit exactly at the seam the offline rule requires: a
:class:`FakeMcpServer` speaks real JSON-RPC over real ``asyncio`` streams, and the
network senders return scripted bytes. Nothing here opens a socket or spawns a
process, and the client under test cannot tell.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from typing import Any

from ronin.core.types import DangerLevel
from ronin.mcp.client import PROTOCOL_VERSION, TransportProvider
from ronin.mcp.config import McpServerConfig, TransportKind
from ronin.mcp.jsonrpc import (
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    JsonRpcError,
    ProtocolError,
    Request,
    Response,
    TransportClosed,
    encode,
    error_response,
    parse_request,
)
from ronin.mcp.transport import StdioTransport, StreamPair, memory_duplex, read_frame

#: A scripted ``tools/call`` handler: arguments in, the *result mapping* out, so a
#: test can return ``{"isError": True, ...}`` as easily as a success.
ToolHandler = Callable[[Mapping[str, Any]], Mapping[str, Any]]


def tool_descriptor(
    name: str,
    *,
    description: str = "Does a thing.",
    schema: Mapping[str, Any] | None = None,
    annotations: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """One entry of a ``tools/list`` reply."""
    descriptor: dict[str, Any] = {
        "name": name,
        "description": description,
        "inputSchema": schema
        if schema is not None
        else {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    }
    if annotations is not None:
        descriptor["annotations"] = dict(annotations)
    return descriptor


def text_result(text: str) -> Mapping[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": False}


def echo(prefix: str = "echo") -> ToolHandler:
    def handler(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        return text_result(f"{prefix}:{sorted(arguments.items())}")

    return handler


class FakeMcpServer:
    """A scriptable MCP server, stdio-shaped, over in-process streams.

    ``kill`` closes every open pipe *and* stops serving, which is what a crashed
    subprocess looks like from the client's side. Cancelling the serve task alone
    would produce a hang, which is a different failure with a different recovery.
    """

    def __init__(
        self,
        name: str = "fake",
        *,
        descriptors: Sequence[Mapping[str, Any]] = (),
        handlers: Mapping[str, ToolHandler] | None = None,
        capabilities: Sequence[str] = ("tools",),
        protocol_version: str = PROTOCOL_VERSION,
        extra_pages: Sequence[Sequence[Mapping[str, Any]]] = (),
        inject: bytes = b"",
        die_after: int | None = None,
    ) -> None:
        # `die_after` counts answered requests across every connection and fires
        # once: "the server dropped the pipe mid-session and came back".
        self.name = name
        self.descriptors = [dict(entry) for entry in descriptors]
        self.handlers = dict(handlers or {})
        self.capabilities = tuple(capabilities)
        self.protocol_version = protocol_version
        self.extra_pages = [list(page) for page in extra_pages]
        self.inject = inject
        self.die_after = die_after
        self.dead = False
        self.died_once = False
        self.served = 0
        self.connections = 0
        self.requests: list[Request] = []
        self._open: list[tuple[asyncio.Task[None], StreamPair]] = []

    # ------------------------------------------------------------ lifecycle --

    def open(self) -> StreamPair:
        """The client's end of a new connection. Already at EOF once killed."""
        self.connections += 1
        near, far = memory_duplex()
        if self.dead:
            far.writer.close()
            return near
        self._open.append((asyncio.create_task(self._serve(far)), far))
        return near

    def kill(self) -> None:
        self.dead = True
        for task, far in self._open:
            far.writer.close()
            task.cancel()
        self._open.clear()

    def provider(self, *, timeout: float = 2.0) -> TransportProvider:
        def build(config: McpServerConfig) -> StdioTransport:
            return StdioTransport(streams=self.open(), timeout=timeout)

        return build

    # -------------------------------------------------------------- serving --

    async def _serve(self, streams: StreamPair) -> None:
        if self.inject:
            streams.writer.write(self.inject)
            await streams.writer.drain()
        while not self.dead:
            try:
                frame = await read_frame(streams.reader)
            except (TransportClosed, ProtocolError):
                return
            try:
                request = parse_request(frame)
            except JsonRpcError as exc:
                if not await self._write(streams, encode(error_response(None, exc))):
                    return
                continue
            self.requests.append(request)
            if request.is_notification:
                continue
            self.served += 1
            if self.die_after is not None and not self.died_once and self.served > self.die_after:
                # Dies exactly once, counting across connections, so a test can
                # assert that a *successful* reconnect recovers the tool rather
                # than dying again on the retry.
                self.died_once = True
                streams.writer.close()
                return
            response = self._answer(request)
            if not await self._write(streams, encode(response)):
                return
        streams.writer.close()

    def _answer(self, request: Request) -> Response:
        try:
            return Response(id=request.id, result=self._result(request))
        except JsonRpcError as exc:
            return error_response(request.id, exc)

    def _result(self, request: Request) -> Mapping[str, Any]:
        if request.method == "initialize":
            return {
                "protocolVersion": self.protocol_version,
                "capabilities": {key: {} for key in self.capabilities},
                "serverInfo": {"name": self.name, "version": "fake"},
            }
        if request.method == "ping":
            return {}
        if request.method == "tools/list":
            return self._list(request.params.get("cursor"))
        if request.method == "tools/call":
            name = request.params.get("name")
            handler = self.handlers.get(str(name))
            if handler is None:
                raise JsonRpcError(INVALID_PARAMS, f"no tool called {name!r}")
            arguments = request.params.get("arguments")
            return handler(arguments if isinstance(arguments, Mapping) else {})
        raise JsonRpcError(METHOD_NOT_FOUND, f"{self.name} does not implement {request.method!r}")

    def _list(self, cursor: object) -> Mapping[str, Any]:
        pages: list[list[dict[str, Any]]] = [self.descriptors]
        pages.extend([dict(entry) for entry in page] for page in self.extra_pages)
        index = int(cursor) if isinstance(cursor, str) and cursor.isdigit() else 0
        index = min(index, len(pages) - 1)
        result: dict[str, Any] = {"tools": pages[index]}
        if index + 1 < len(pages):
            result["nextCursor"] = str(index + 1)
        return result

    async def _write(self, streams: StreamPair, payload: bytes) -> bool:
        try:
            streams.writer.write(payload)
            await streams.writer.drain()
        except (TransportClosed, OSError, RuntimeError):
            return False
        return True


def server_config(
    name: str = "fake",
    *,
    transport: TransportKind = TransportKind.STDIO,
    danger_level: DangerLevel | None = None,
    requires_approval: bool | None = None,
    url: str = "https://example.invalid/mcp",
    timeout_seconds: float = 2.0,
) -> McpServerConfig:
    """A config for a fake server, without going through JSON."""
    if transport is TransportKind.STDIO:
        return McpServerConfig(
            name=name,
            transport=transport,
            command="fake",
            danger_level=danger_level,
            requires_approval=requires_approval,
            timeout_seconds=timeout_seconds,
        )
    return McpServerConfig(
        name=name,
        transport=transport,
        url=url,
        danger_level=danger_level,
        requires_approval=requires_approval,
        timeout_seconds=timeout_seconds,
    )


def multi_provider(
    fakes: Mapping[str, FakeMcpServer], *, timeout: float = 2.0
) -> TransportProvider:
    """A provider that routes by server name, for multi-server tests."""

    def build(config: McpServerConfig) -> StdioTransport:
        return StdioTransport(streams=fakes[config.name].open(), timeout=timeout)

    return build


# --------------------------------------------------------------------------- #
# Network senders
# --------------------------------------------------------------------------- #


class ScriptedSender:
    """An :class:`~ronin.mcp.transport.HttpSender` that replays bytes per request.

    ``chunk_size`` is the point of the class: setting it to 1 feeds the reader one
    byte at a time, which is how the provider layer's real SSE bugs were found.
    """

    def __init__(
        self,
        replies: Sequence[bytes],
        *,
        chunk_size: int = 0,
    ) -> None:
        self.replies = list(replies)
        self.chunk_size = chunk_size
        self.posted: list[Mapping[str, Any]] = []

    async def post(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
    ) -> AsyncIterator[bytes]:
        self.posted.append(dict(body))
        payload = self.replies[min(len(self.posted) - 1, len(self.replies) - 1)]
        if self.chunk_size <= 0:
            yield payload
            return
        for start in range(0, len(payload), self.chunk_size):
            yield payload[start : start + self.chunk_size]


class _Raiser:
    """An async iterator whose first step raises.

    A class rather than ``async def ... raise ...; yield`` because a generator
    with an unreachable ``yield`` is exactly the shape ``warn_unreachable``
    objects to, and silencing it would be silencing a check that is usually right.
    """

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def __aiter__(self) -> _Raiser:
        return self

    async def __anext__(self) -> bytes:
        raise self._exc


class FailingSender:
    """A sender that raises, for the transport's failure path."""

    def __init__(self, exc: BaseException | None = None) -> None:
        self.exc = exc or OSError("connection reset by peer")
        self.calls = 0

    def post(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
    ) -> AsyncIterator[bytes]:
        self.calls += 1
        return _Raiser(self.exc)


def sse_bytes(*payloads: str, event: str = "message") -> bytes:
    """SSE frames for the given ``data:`` payloads, blank-line separated."""
    return b"".join(f"event: {event}\ndata: {payload}\n\n".encode() for payload in payloads)
