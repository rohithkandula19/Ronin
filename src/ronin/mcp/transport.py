"""Framing: the three ways a JSON-RPC message reaches an MCP server.

:class:`Transport` is the seam. Above it, :mod:`ronin.mcp.client` only ever says
"send this request, give me the response"; below it, three framings that have
nothing in common:

* **stdio** — newline-delimited JSON on a subprocess's pipes. The framing is
  trivial and the failure mode is not: a server that prints a banner to stdout
  corrupts the stream, and a server that dies leaves a half-read line.
* **SSE** — the response body is an ``event:``/``data:`` stream. Chunk boundaries
  land wherever the network puts them: mid-line, between ``\\r`` and ``\\n``,
  mid-UTF-8-character. Ronin's provider layer shipped bugs in exactly this code
  path, found by feeding a fixture one byte at a time, so :class:`SseFrameReader`
  is tested the same way.
* **HTTP** — one POST, one JSON body. The boring one.

Nothing here opens a socket or spawns a process unless you ask it to. The network
transports take an injected :class:`HttpSender`, and stdio takes either a command
*or* a :class:`StreamPair`, which is how the whole test suite and the demo run
offline with no subprocesses at all.

This module deliberately does **not** import ``ronin.providers``, whose
``SSEReader`` solves a neighbouring problem (single-line ``data:`` payloads for a
model stream). The reader below dispatches on blank lines and keeps ``event:``
names, per the SSE spec, because MCP puts more than one message on a stream.
"""

from __future__ import annotations

import asyncio
import sys
from collections import deque
from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import IO, Any, Protocol

from .jsonrpc import (
    JsonRpcError,
    McpError,
    ProtocolError,
    Request,
    Response,
    TransportClosed,
    encode,
    parse_response,
)

#: Hard ceiling on a single frame, both directions. A peer that sends more is
#: either broken or hostile; either way the connection cannot continue, because
#: after an over-long line there is no way to know where the next one begins.
MAX_FRAME_BYTES = 4 * 1024 * 1024

#: The buffer an ``asyncio`` reader is given. Larger than the frame ceiling so
#: that *we* reject an oversized frame with a message naming the limit, rather
#: than ``readline`` raising an opaque ``ValueError`` first.
READER_LIMIT = MAX_FRAME_BYTES * 2

#: Default per-request patience. An MCP tool call can legitimately take a while
#: (a remote search, a build), so this is generous; the point is that it is
#: finite, because a hung server must not wedge the agent's turn forever.
DEFAULT_TIMEOUT_SECONDS = 30.0

#: How much of a dead stdio server's stderr to keep. When a server exits, its
#: last words are the only actionable thing we have to put in front of the model.
STDERR_TAIL_LINES = 20


class TransportTimeout(McpError):
    """A request went unanswered for longer than the configured timeout."""


# --------------------------------------------------------------------------- #
# Streams
# --------------------------------------------------------------------------- #


class ByteReader(Protocol):
    """The read half of a byte stream. ``asyncio.StreamReader`` satisfies this."""

    async def readline(self) -> bytes:
        """One line including its terminator, or ``b""`` at EOF."""
        ...


class ByteWriter(Protocol):
    """The write half. ``asyncio.StreamWriter`` satisfies this."""

    def write(self, data: bytes, /) -> None: ...

    async def drain(self) -> None: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class StreamPair:
    """One end of a duplex connection.

    The unit of injection: a test or the demo hands a :class:`StdioTransport` a
    pair wired to an in-process server, and the transport cannot tell the
    difference from a real subprocess.
    """

    reader: ByteReader
    writer: ByteWriter


class PipeWriter:
    """A :class:`ByteWriter` that feeds an ``asyncio.StreamReader`` in-process.

    Exists so "the server died" is expressible offline: :meth:`close` feeds EOF,
    which is precisely what the peer sees when a subprocess exits.
    """

    def __init__(self, target: asyncio.StreamReader) -> None:
        self._target = target
        self._closed = False

    def write(self, data: bytes, /) -> None:
        if self._closed:
            raise TransportClosed("write to a closed in-process pipe")
        self._target.feed_data(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._target.feed_eof()

    @property
    def closed(self) -> bool:
        return self._closed


class FileWriter:
    """A :class:`ByteWriter` over a binary file object — this process's stdout.

    Two departures from :class:`asyncio.StreamWriter`, both deliberate:

    * **The write is blocking.** The canonical async recipe is
      ``loop.connect_write_pipe(asyncio.streams.FlowControlMixin, …)``, which reaches
      into a private class. The cost of not doing that is that a frame larger than the
      pipe buffer blocks the loop until the client drains it — and a served session is
      strictly sequential (read a frame, answer it, read the next), so there is nothing
      the loop could usefully run in the meantime anyway.
    * **:meth:`close` does not close the file.** The file is the process's stdout, which
      the rest of the process — logging, a traceback, an exit message — still needs.
      Closing it would turn a finished MCP session into a process that cannot report
      anything about itself. The flag stops further writes; the fd stays open.
    """

    def __init__(self, stream: IO[bytes]) -> None:
        self._stream = stream
        self._closed = False

    def write(self, data: bytes, /) -> None:
        if self._closed:
            raise TransportClosed("write to a closed stdio writer")
        try:
            self._stream.write(data)
        except (OSError, ValueError) as exc:
            # ValueError is what a file object raises once it is closed, and it is not
            # an OSError. A client that exits mid-frame produces exactly this.
            self._closed = True
            raise TransportClosed(f"writing to stdout failed: {exc}") from exc

    async def drain(self) -> None:
        if self._closed:
            raise TransportClosed("flush of a closed stdio writer")
        try:
            self._stream.flush()
        except (OSError, ValueError) as exc:
            self._closed = True
            raise TransportClosed(f"flushing stdout failed: {exc}") from exc

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with suppress(OSError, ValueError):
            self._stream.flush()

    @property
    def closed(self) -> bool:
        return self._closed


def memory_duplex(*, limit: int = READER_LIMIT) -> tuple[StreamPair, StreamPair]:
    """Two connected :class:`StreamPair` ends, entirely in memory.

    Returns ``(near, far)``: what ``near`` writes, ``far`` reads. Must be called
    from inside a running loop, because ``asyncio.StreamReader`` binds to it.
    """
    to_far = asyncio.StreamReader(limit=limit)
    to_near = asyncio.StreamReader(limit=limit)
    near = StreamPair(reader=to_near, writer=PipeWriter(to_far))
    far = StreamPair(reader=to_far, writer=PipeWriter(to_near))
    return near, far


async def stdio_streams(
    *,
    stdin: IO[bytes] | None = None,
    stdout: IO[bytes] | None = None,
    limit: int = READER_LIMIT,
) -> StreamPair:
    """This process's own stdin and stdout, as a :class:`StreamPair`.

    The other half of :func:`memory_duplex`: the same type, wired to real pipes, so
    :func:`ronin.mcp.server.serve_stdio` does not know or care which end of the seam it
    is on. This is what a client that *spawns* Ronin — Claude Desktop, Cursor, another
    Ronin — connects to.

    Reading goes through ``loop.connect_read_pipe`` rather than a blocking
    ``stdin.readline()``, and that is the half that has to be async: waiting for the
    next request is unbounded, and a blocked loop is a session whose shell, background
    jobs and MCP clients are all frozen while it waits. Writing is
    :class:`FileWriter` — see its docstring for why blocking is acceptable there.

    Raises :class:`~ronin.mcp.jsonrpc.TransportClosed`, naming the reason, on a platform
    that cannot poll a pipe (Windows' proactor loop refuses ``connect_read_pipe`` for
    anything that is not a socket). A named error is the point: the alternative is a
    server that appears to start and then never answers.

    Both file objects are injectable so the tests drive this over a real ``os.pipe()``
    without touching the interpreter's own streams.
    """
    source = _binary(sys.stdin, "stdin") if stdin is None else stdin
    sink = _binary(sys.stdout, "stdout") if stdout is None else stdout
    # Asked before ``connect_read_pipe`` rather than left to it. A stream with no
    # descriptor — pytest's capture, a StringIO, a closed file — makes asyncio raise from
    # inside a half-built transport whose ``__del__`` then emits its own unrelated
    # warning, which buries the actual reason under a second traceback.
    try:
        source.fileno()
    except (AttributeError, OSError, ValueError) as exc:
        raise TransportClosed(
            f"stdin has no file descriptor ({type(exc).__name__}: {exc}), so it cannot "
            "carry an MCP session. This process's stdin has been replaced by an "
            "in-memory object; serve on injected streams instead."
        ) from exc
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=limit)
    try:
        await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), source)
    except (NotImplementedError, OSError, ValueError) as exc:
        raise TransportClosed(
            f"cannot read stdin asynchronously ({type(exc).__name__}: {exc}). An MCP "
            "stdio server needs a real pipe or terminal on fd 0; this happens when "
            "stdin has been replaced by an object with no file descriptor, or on a "
            "platform whose event loop cannot poll a pipe."
        ) from exc
    return StreamPair(reader=reader, writer=FileWriter(sink))


def _binary(stream: object, name: str) -> IO[bytes]:
    """The byte layer under a text stream.

    ``sys.stdout`` is a ``TextIOWrapper`` with a buffer of its own, and writing bytes
    past it would interleave with anything the text layer has not flushed. Taking
    ``.buffer`` means one writer for the stream. A replaced stream that has no
    ``buffer`` (``StringIO``, pytest's capture) is refused here rather than half-working.
    """
    buffer = getattr(stream, "buffer", None)
    if buffer is None:
        raise TransportClosed(
            f"sys.{name} has no binary buffer, so this process cannot serve MCP over "
            f"stdio. It has been replaced by a text-only object ({type(stream).__name__})."
        )
    return buffer  # type: ignore[no-any-return]  # a TextIOWrapper's buffer is IO[bytes]


async def read_frame(reader: ByteReader, *, max_bytes: int = MAX_FRAME_BYTES) -> bytes:
    """One newline-delimited frame, minus the terminator.

    Raises :class:`TransportClosed` at EOF and :class:`ProtocolError` for a frame
    over ``max_bytes``. The second case is fatal on purpose: ``readline`` leaves
    the overrun in the buffer, so there is no resync point and pretending
    otherwise would mean silently interpreting the tail of a giant payload as a
    fresh message.
    """
    try:
        line = await reader.readline()
    except ValueError as exc:  # asyncio raises this for a line past its limit
        raise ProtocolError(f"a peer sent a frame longer than the reader's buffer: {exc}") from exc
    if not line:
        raise TransportClosed("stream reached EOF")
    if len(line) > max_bytes:
        raise ProtocolError(
            f"frame of {len(line)} bytes exceeds the {max_bytes}-byte limit; "
            "the connection cannot be resynchronised and is now closed"
        )
    return line.rstrip(b"\r\n")


# --------------------------------------------------------------------------- #
# Server-sent events
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class SseFrame:
    """One dispatched SSE event. ``event`` is ``"message"`` when unspecified."""

    event: str
    data: str


class SseFrameReader:
    """Reassembles SSE frames from bytes split at arbitrary boundaries.

    Buffers *bytes* and only decodes complete lines, which is what makes a chunk
    boundary in the middle of a multi-byte character harmless — no UTF-8
    continuation byte is ``\\n`` or ``\\r``, so a line is always a whole sequence
    of characters even when the chunking is adversarial.

    Follows the spec on the two things that matter for MCP: a frame is dispatched
    on a *blank line*, and repeated ``data:`` fields are joined with ``\\n``. The
    single-``data:``-line shortcut some clients take drops the second half of any
    pretty-printed payload.
    """

    def __init__(self) -> None:
        self._pending = b""
        self._data: list[str] = []
        self._event = ""

    def feed(self, chunk: bytes) -> list[SseFrame]:
        """The frames completed by ``chunk``, in order. Usually zero or one."""
        self._pending += chunk
        frames: list[SseFrame] = []
        while True:
            line, rest = _split_line(self._pending)
            if line is None:
                break
            self._pending = rest
            frame = self._consume(line)
            if frame is not None:
                frames.append(frame)
        return frames

    def finish(self) -> list[SseFrame]:
        """Flush a stream that ended without a final blank line.

        Real servers close right after the last ``data:`` line often enough that
        discarding it would lose the response to the last request.
        """
        frames: list[SseFrame] = []
        if self._pending:
            line, self._pending = self._pending, b""
            frame = self._consume(line)
            if frame is not None:
                frames.append(frame)
        if self._data:
            frames.append(self._dispatch())
        return frames

    def _consume(self, line: bytes) -> SseFrame | None:
        text = line.decode("utf-8", errors="replace")
        if not text:
            return self._dispatch() if self._data else None
        if text.startswith(":"):
            return None  # a comment / keep-alive
        field, _, value = text.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if field == "data":
            self._data.append(value)
        elif field == "event":
            self._event = value
        # `id:` and `retry:` are reconnection bookkeeping we do not use; a field
        # we do not know is ignored, which is what the spec requires.
        return None

    def _dispatch(self) -> SseFrame:
        frame = SseFrame(event=self._event or "message", data="\n".join(self._data))
        self._data = []
        self._event = ""
        return frame


def _split_line(buffer: bytes) -> tuple[bytes | None, bytes]:
    """The first complete line in ``buffer``, honouring ``\\n``, ``\\r\\n``, ``\\r``.

    A lone trailing ``\\r`` is *not* a complete line: the ``\\n`` that would pair
    with it may be the first byte of the next chunk, and treating the ``\\r`` as a
    terminator turns one frame into two empty ones — which, since a blank line
    dispatches a frame, silently truncates the payload.
    """
    index = min((i for i in (buffer.find(b"\n"), buffer.find(b"\r")) if i >= 0), default=-1)
    if index < 0:
        return None, buffer
    if buffer[index : index + 1] == b"\r":
        if index == len(buffer) - 1:
            return None, buffer
        skip = 2 if buffer[index + 1 : index + 2] == b"\n" else 1
        return buffer[:index], buffer[index + skip :]
    return buffer[:index], buffer[index + 1 :]


async def sse_responses(chunks: AsyncIterator[bytes]) -> AsyncIterator[Response]:
    """Every JSON-RPC response carried by an SSE byte stream.

    Frames whose data is not a JSON-RPC response are skipped rather than fatal:
    gateways inject keep-alives, and MCP servers send ``notifications/message``
    log frames down the same stream. Killing a turn over a log line would be a
    self-inflicted outage.
    """
    reader = SseFrameReader()
    async for chunk in chunks:
        for frame in reader.feed(chunk):
            response = _response_or_none(frame)
            if response is not None:
                yield response
    for frame in reader.finish():
        response = _response_or_none(frame)
        if response is not None:
            yield response


def _response_or_none(frame: SseFrame) -> Response | None:
    if not frame.data or frame.data == "[DONE]":
        return None
    try:
        return parse_response(frame.data)
    except (ProtocolError, JsonRpcError):
        return None


# --------------------------------------------------------------------------- #
# The contract
# --------------------------------------------------------------------------- #


class Transport(Protocol):
    """Send a request, get its response. Framing is the implementation's problem."""

    async def start(self) -> None:
        """Open the connection. Idempotent."""
        ...

    async def send(self, request: Request) -> Response:
        """Round-trip one request. Raises rather than returning an error response
        only when the *transport* failed; a JSON-RPC ``error`` comes back as a
        :class:`Response`."""
        ...

    async def notify(self, request: Request) -> None:
        """Fire one notification. No answer is read, because none is coming."""
        ...

    async def close(self) -> None:
        """Release everything. Idempotent, and safe on an already-dead peer."""
        ...

    @property
    def alive(self) -> bool:
        """False once a failure has been seen. Never becomes True again — a new
        connection is a new transport, which is what makes reconnect auditable."""
        ...

    @property
    def detail(self) -> str:
        """Whatever the transport knows about why it died. May be empty."""
        ...


class HttpSender(Protocol):
    """POST a JSON body and yield the response body's bytes.

    The same shape serves both network transports: :class:`HttpTransport`
    concatenates the chunks and parses one JSON object, :class:`SseTransport`
    parses SSE frames out of them.
    """

    def post(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
    ) -> AsyncIterator[bytes]: ...


# --------------------------------------------------------------------------- #
# stdio
# --------------------------------------------------------------------------- #


class StdioTransport:
    """Newline-delimited JSON over a subprocess's stdin/stdout.

    Requests are serialised by a lock. Pipelining several in flight and matching
    responses by id would be faster, but a stdio MCP server is a single process
    doing one thing at a time anyway, and an interleaved reader is the kind of bug
    that only shows up under load.

    A non-JSON line is treated as fatal rather than skipped. Servers that print
    banners to stdout exist, and skipping junk would paper over them — at the cost
    that a *response* which happens to follow the banner can no longer be matched
    to its request. Failing loudly here is safe because the client above turns it
    into a failed ``ToolResult`` plus one reconnect attempt, so the session lives.
    """

    def __init__(
        self,
        *,
        streams: StreamPair | None = None,
        command: Sequence[str] = (),
        env: Mapping[str, str] | None = None,
        cwd: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_frame_bytes: int = MAX_FRAME_BYTES,
    ) -> None:
        # env=None means "inherit whatever this process has". Nothing here reads
        # os.environ: a session that wants a curated environment injects one.
        if streams is None and not command:
            raise ValueError(
                "StdioTransport needs either streams (in-process, for tests and "
                "the demo) or a command to spawn"
            )
        if streams is not None and command:
            raise ValueError("StdioTransport takes streams or a command, not both")
        self._streams = streams
        self._command = tuple(command)
        self._env = dict(env) if env is not None else None
        self._cwd = cwd
        self._timeout = timeout
        self._max_frame_bytes = max_frame_bytes
        self._process: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._stderr: deque[str] = deque(maxlen=STDERR_TAIL_LINES)
        self._lock = asyncio.Lock()
        self._alive = True
        self._detail = ""
        self._started = False
        self._next_id = 0

    @property
    def alive(self) -> bool:
        return self._alive

    @property
    def detail(self) -> str:
        if self._stderr and not self._detail:
            return "server stderr: " + " | ".join(self._stderr)
        if self._stderr:
            return f"{self._detail}; server stderr: " + " | ".join(self._stderr)
        return self._detail

    async def start(self) -> None:
        if self._started:
            return
        if self._streams is None:
            self._streams = await self._spawn()
        self._started = True

    async def _spawn(self) -> StreamPair:
        try:
            process = await asyncio.create_subprocess_exec(
                *self._command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._env,
                cwd=self._cwd,
                limit=READER_LIMIT,
            )
        except OSError as exc:
            self._fail(f"could not start {self._command[0]!r}: {exc}")
            raise TransportClosed(self.detail) from exc
        if process.stdin is None or process.stdout is None:
            self._fail("the spawned process has no stdin/stdout pipes")
            raise TransportClosed(self.detail)
        self._process = process
        if process.stderr is not None:
            # Drained in the background: a server that logs verbosely fills the
            # pipe buffer and then blocks forever on its next write, which looks
            # exactly like a hung server and is impossible to diagnose from here.
            self._stderr_task = asyncio.create_task(_drain(process.stderr, self._stderr))
        return StreamPair(reader=process.stdout, writer=process.stdin)

    def next_id(self) -> int:
        self._next_id += 1
        return self._next_id

    async def send(self, request: Request) -> Response:
        if request.is_notification:
            raise ValueError("send() needs a request with an id; use notify()")
        async with self._lock:
            streams = self._require_streams()
            await self._write(streams, request)
            try:
                return await asyncio.wait_for(
                    self._read_matching(streams, request), timeout=self._timeout
                )
            except TimeoutError as exc:
                self._fail(f"no response to {request.method!r} within {self._timeout}s")
                raise TransportTimeout(self.detail) from exc

    async def notify(self, request: Request) -> None:
        if not request.is_notification:
            raise ValueError("notify() needs a request with no id; use send()")
        async with self._lock:
            await self._write(self._require_streams(), request)

    async def _write(self, streams: StreamPair, request: Request) -> None:
        try:
            streams.writer.write(encode(request))
            await streams.writer.drain()
        except (OSError, TransportClosed, RuntimeError) as exc:
            self._fail(f"writing {request.method!r} failed: {exc}")
            raise TransportClosed(self.detail) from exc

    async def _read_matching(self, streams: StreamPair, request: Request) -> Response:
        """Responses until one matches ``request.id``.

        A server may interleave its own requests and notifications (sampling,
        progress, logging) with responses. Those are skipped: this client
        advertises no capabilities that would make a server request meaningful,
        and answering one we do not implement is worse than ignoring it.
        """
        while True:
            try:
                frame = await read_frame(streams.reader, max_bytes=self._max_frame_bytes)
            except (TransportClosed, ProtocolError) as exc:
                self._fail(str(exc))
                raise
            try:
                response = parse_response(frame)
            except (ProtocolError, JsonRpcError) as exc:
                if _looks_like_server_message(frame):
                    continue
                self._fail(
                    f"the server wrote a line that is not a JSON-RPC response "
                    f"({exc}); an MCP stdio server must keep stdout free of logs "
                    f"and banners and use stderr instead. Offending line: "
                    f"{frame[:200]!r}"
                )
                raise ProtocolError(self.detail) from exc
            if response.id == request.id:
                return response

    def _require_streams(self) -> StreamPair:
        if not self._alive:
            raise TransportClosed(self.detail or "transport is already dead")
        if self._streams is None:
            raise TransportClosed("transport was never started")
        return self._streams

    def _fail(self, detail: str) -> None:
        self._alive = False
        if not self._detail:
            self._detail = detail

    async def close(self) -> None:
        self._alive = False
        streams, self._streams = self._streams, None
        if streams is not None:
            with suppress(OSError, RuntimeError):
                streams.writer.close()
        process, self._process = self._process, None
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=2.0)
            except (TimeoutError, ProcessLookupError):
                with suppress(ProcessLookupError, OSError):
                    process.kill()
        task, self._stderr_task = self._stderr_task, None
        if task is not None:
            task.cancel()


def _looks_like_server_message(frame: bytes) -> bool:
    """Whether a frame is a server-initiated request/notification, not a response.

    Those are valid JSON-RPC and must be skipped, not treated as stream
    corruption — distinguishing them is why ``parse_response`` failing is not on
    its own enough to declare the server broken.
    """
    return b'"method"' in frame and b'"jsonrpc"' in frame


async def _drain(reader: asyncio.StreamReader, sink: deque[str]) -> None:
    while True:
        try:
            line = await reader.readline()
        except (ValueError, OSError):
            return
        if not line:
            return
        text = line.decode("utf-8", errors="replace").rstrip()
        if text:
            sink.append(text)


# --------------------------------------------------------------------------- #
# HTTP and SSE
# --------------------------------------------------------------------------- #


class HttpTransport:
    """One POST per request; the whole response body is one JSON object."""

    def __init__(
        self,
        *,
        url: str,
        sender: HttpSender,
        headers: Mapping[str, str] | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_frame_bytes: int = MAX_FRAME_BYTES,
    ) -> None:
        if not url:
            raise ValueError("HttpTransport needs a url")
        self._url = url
        self._sender = sender
        self._headers = {**_JSON_HEADERS, **dict(headers or {})}
        self._timeout = timeout
        self._max_frame_bytes = max_frame_bytes
        self._alive = True
        self._detail = ""
        self._next_id = 0

    @property
    def alive(self) -> bool:
        return self._alive

    @property
    def detail(self) -> str:
        return self._detail

    async def start(self) -> None:
        return None

    def next_id(self) -> int:
        self._next_id += 1
        return self._next_id

    async def send(self, request: Request) -> Response:
        if request.is_notification:
            raise ValueError("send() needs a request with an id; use notify()")
        body = await self._post(request)
        try:
            return parse_response(body)
        except (ProtocolError, JsonRpcError) as exc:
            self._fail(f"{self._url} answered {request.method!r} with a non-response: {exc}")
            raise ProtocolError(self._detail) from exc

    async def notify(self, request: Request) -> None:
        if not request.is_notification:
            raise ValueError("notify() needs a request with no id; use send()")
        # A notification's body is discarded, but it is still read: an HTTP
        # implementation that abandons the response mid-flight can leave the
        # connection unusable for the next request.
        await self._post(request)

    async def _post(self, request: Request) -> bytes:
        if not self._alive:
            raise TransportClosed(self._detail or "transport is already dead")
        try:
            return await asyncio.wait_for(self._collect(request), timeout=self._timeout)
        except TimeoutError as exc:
            self._fail(f"{self._url} did not answer {request.method!r} within {self._timeout}s")
            raise TransportTimeout(self._detail) from exc
        except (OSError, McpError) as exc:
            self._fail(f"{self._url} failed on {request.method!r}: {exc}")
            raise TransportClosed(self._detail) from exc

    async def _collect(self, request: Request) -> bytes:
        parts: list[bytes] = []
        size = 0
        async for chunk in self._sender.post(
            url=self._url, headers=self._headers, body=request.to_wire()
        ):
            size += len(chunk)
            if size > self._max_frame_bytes:
                raise ProtocolError(
                    f"response body exceeded the {self._max_frame_bytes}-byte limit"
                )
            parts.append(chunk)
        return b"".join(parts)

    async def close(self) -> None:
        self._alive = False

    def _fail(self, detail: str) -> None:
        self._alive = False
        if not self._detail:
            self._detail = detail


class SseTransport:
    """One POST per request; the response arrives as ``text/event-stream`` frames.

    This is the shape MCP's streamable-HTTP transport uses when a server chooses
    to stream: the POST's own body carries the answer, possibly preceded by
    progress and log notifications. The first frame whose id matches wins, and
    everything else on the stream is discarded — a client that took the *first*
    frame would return a progress notification as if it were the result.
    """

    def __init__(
        self,
        *,
        url: str,
        sender: HttpSender,
        headers: Mapping[str, str] | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not url:
            raise ValueError("SseTransport needs a url")
        self._url = url
        self._sender = sender
        self._headers = {**_SSE_HEADERS, **dict(headers or {})}
        self._timeout = timeout
        self._alive = True
        self._detail = ""
        self._next_id = 0

    @property
    def alive(self) -> bool:
        return self._alive

    @property
    def detail(self) -> str:
        return self._detail

    async def start(self) -> None:
        return None

    def next_id(self) -> int:
        self._next_id += 1
        return self._next_id

    async def send(self, request: Request) -> Response:
        if request.is_notification:
            raise ValueError("send() needs a request with an id; use notify()")
        if not self._alive:
            raise TransportClosed(self._detail or "transport is already dead")
        try:
            return await asyncio.wait_for(self._stream(request), timeout=self._timeout)
        except TimeoutError as exc:
            self._fail(f"{self._url} did not answer {request.method!r} within {self._timeout}s")
            raise TransportTimeout(self._detail) from exc
        except (OSError, McpError) as exc:
            self._fail(f"{self._url} failed on {request.method!r}: {exc}")
            raise TransportClosed(self._detail) from exc

    async def _stream(self, request: Request) -> Response:
        chunks = self._sender.post(url=self._url, headers=self._headers, body=request.to_wire())
        async for response in sse_responses(chunks):
            if response.id == request.id:
                return response
        raise ProtocolError(
            f"the event stream for {request.method!r} ended without a response "
            f"carrying id {request.id!r}"
        )

    async def notify(self, request: Request) -> None:
        if not request.is_notification:
            raise ValueError("notify() needs a request with no id; use send()")
        chunks = self._sender.post(url=self._url, headers=self._headers, body=request.to_wire())
        async for _ in chunks:
            pass

    async def close(self) -> None:
        self._alive = False

    def _fail(self, detail: str) -> None:
        self._alive = False
        if not self._detail:
            self._detail = detail


_JSON_HEADERS: Mapping[str, str] = {
    "content-type": "application/json",
    "accept": "application/json",
}

_SSE_HEADERS: Mapping[str, str] = {
    "content-type": "application/json",
    "accept": "text/event-stream, application/json",
}


class HttpxSender:
    """The only class in this package that can touch a network.

    ``httpx`` is imported inside :meth:`post`, so a bare install stays importable
    and the whole test suite — which never constructs this — needs nothing. Not
    exercised by the suite for the same reason it exists: the ``$0``/offline rule
    forbids a test that opens a socket.
    """

    def __init__(self, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self._timeout = timeout

    async def post(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        body: Mapping[str, Any],
    ) -> AsyncIterator[bytes]:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - absence, not a test path
            raise TransportClosed(
                "httpx is required for the sse and http MCP transports; stdio "
                "servers and the test suite do not need it"
            ) from exc
        async with (
            httpx.AsyncClient(timeout=self._timeout) as client,
            client.stream("POST", url, headers=dict(headers), json=dict(body)) as resp,
        ):
            if resp.status_code >= 400:
                detail = (await resp.aread()).decode("utf-8", errors="replace")
                raise TransportClosed(f"{url} returned {resp.status_code}: {detail[:500]}")
            async for chunk in resp.aiter_bytes():
                yield chunk


def join_url(base_url: str, path: str) -> str:
    """Join a configured base URL with an endpoint path.

    Same shape as the provider layer's helper of the same name, reimplemented in
    two lines rather than imported: ``mcp/`` must not depend on ``providers/``.
    """
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def frames_of(chunks: Iterable[bytes]) -> list[SseFrame]:
    """Every SSE frame in a finite, already-materialised byte sequence.

    A synchronous convenience for fixtures and the demo; the streaming path is
    :func:`sse_responses`.
    """
    reader = SseFrameReader()
    frames: list[SseFrame] = []
    for chunk in chunks:
        frames.extend(reader.feed(chunk))
    frames.extend(reader.finish())
    return frames


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_FRAME_BYTES",
    "READER_LIMIT",
    "STDERR_TAIL_LINES",
    "ByteReader",
    "ByteWriter",
    "FileWriter",
    "HttpSender",
    "HttpTransport",
    "HttpxSender",
    "PipeWriter",
    "SseFrame",
    "SseFrameReader",
    "SseTransport",
    "StdioTransport",
    "StreamPair",
    "Transport",
    "TransportTimeout",
    "frames_of",
    "join_url",
    "memory_duplex",
    "read_frame",
    "sse_responses",
    "stdio_streams",
]
