"""``ronin acp`` — Ronin as an ACP (Agent Client Protocol) agent over stdio.

An editor — Zed, a JetBrains IDE, anything that speaks ACP — launches this process
and drives a coding session through it: open a session on a directory, send a
prompt, watch the agent's message, its thinking and its tool calls stream back, and
cancel a turn that is going the wrong way. ACP is JSON-RPC 2.0 over stdio, which is
the *same* framing :mod:`ronin.mcp.server` already serves, so this module reuses that
transport and codec whole rather than growing a second one. What is new is the
direction of travel: MCP over stdio only ever *answered* requests, and ACP also
*pushes* — every ``session/update`` is a server-initiated notification onto the same
writer the client's requests arrive on.

Three decisions, each following from the shape of the protocol.

**The framing loop, not the dispatcher, owns the writer.** :class:`AcpServer` is pure
request-in / response-out — :meth:`AcpServer.handle` is the whole method table and is
testable by calling it with a :class:`~ronin.mcp.jsonrpc.Request`. But a ``session/prompt``
has to emit notifications *while it runs*, before its own response exists, so
:func:`serve_acp` injects a ``notify`` coroutine (:meth:`AcpServer.bind`) that the
prompt handler awaits per event. The notification is an ordinary
:class:`~ronin.mcp.jsonrpc.Request` with no id — :func:`~ronin.mcp.jsonrpc.encode`
already serializes that as a notification frame, so nothing here hand-rolls wire
bytes.

**Requests are handled concurrently, so cancel can land.** ``session/cancel`` is a
notification the client sends *during* an in-flight ``session/prompt``. A loop that
blocked on the prompt could never read it. So :func:`serve_acp` spawns each frame's
handler as a task and keeps reading; writes are serialized by a lock, so frames never
interleave mid-line. ``session/cancel`` reaches the runtime through
:meth:`~ronin.safety.policy.PolicyEngine.cancel`, which the turn loop polls at each
iteration boundary — best-effort, because a turn already streaming its final answer
has nothing left to stop.

**The Agent is assembled behind a factory seam.** :class:`AcpServer` never imports a
provider or a router; it is handed an ``open_agent`` coroutine that turns a ``cwd``
into an opened :class:`~ronin.cli.sdk.Agent`. That is what lets the tests drive the
whole server over an in-memory pipe with a scripted model, and lets the ``ronin acp``
subcommand pass a real :meth:`Agent.open`-based factory. One Agent is stored per
session and closed when the server closes.

Nothing here writes to stdout except protocol frames — the banner and any notes go to
stderr, exactly as :mod:`ronin.cli.serve` does, because stdout *is* the wire and one
stray line corrupts the frame stream.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from ..core.loop import StopReason
from ..core.types import Error, Event, TextDelta, ToolEnd, ToolStart, TurnEnd
from ..mcp.jsonrpc import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
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
from ..mcp.transport import MAX_FRAME_BYTES, StreamPair, read_frame
from .sdk import Agent

#: The ACP major version this adapter speaks. ACP is at 1; a client that asks for
#: something else is answered with this and left to decide whether it can live with
#: it, which is what the spec prescribes.
PROTOCOL_VERSION = 1

SERVER_NAME = "ronin"

#: Ronin's version is a property of the distribution, not of this module — the
#: subcommand passes the real one. ``"unknown"`` is a named absence, never a guess.
DEFAULT_SERVER_VERSION = "unknown"

#: Turns a ``cwd`` into an opened :class:`~ronin.cli.sdk.Agent`. Injected, so this
#: module depends on no provider and the tests can hand in a scripted one.
AgentFactory = Callable[[str], Awaitable[Agent]]

#: The sink :func:`serve_acp` gives the server for ``session/update`` notifications.
#: It takes a :class:`~ronin.mcp.jsonrpc.Request` with no id (a notification).
Notifier = Callable[[Request], Awaitable[None]]

#: Ronin's ``TurnEnd.stop_reason`` values, mapped to ACP's ``stopReason`` vocabulary.
#: Anything unlisted is a clean end (``end_turn``); the failure-ish reasons collapse
#: to ``refusal`` because ACP's four values have no dedicated "error", and the
#: :class:`~ronin.core.types.Error` event has already carried the detail across.
_ACP_STOP_REASONS: Mapping[str, str] = {
    StopReason.NO_TOOL_CALLS.value: "end_turn",
    StopReason.INTERRUPTED.value: "cancelled",
    StopReason.TOKEN_BUDGET.value: "max_tokens",
    StopReason.COST_BUDGET.value: "max_tokens",
    StopReason.MAX_ITERATIONS.value: "max_tokens",
    StopReason.STALLED.value: "refusal",
    "provider_error": "refusal",
    "protocol_error": "refusal",
}


def stop_reason_to_acp(reason: str) -> str:
    """Map a Ronin ``TurnEnd.stop_reason`` to an ACP ``stopReason``.

    Total: an empty or unrecognised reason is reported as ``end_turn`` rather than
    invented, because the turn did end and the editor's model has the transcript to
    read regardless.
    """
    return _ACP_STOP_REASONS.get(reason, "end_turn")


def event_to_update(event: Event) -> dict[str, Any] | None:
    """One loop :class:`~ronin.core.types.Event` as an ACP ``update`` object, or ``None``.

    ``None`` means "nothing to push for this event" — the turn-lifecycle events
    (:class:`~ronin.core.types.TurnStart`, :class:`~ronin.core.types.TurnEnd`,
    :class:`~ronin.core.types.StreamReset`, and the middleware's compaction/verify
    markers) carry no text a client renders inline. ``TurnEnd`` in particular is the
    turn's *result*, delivered as the ``session/prompt`` response's ``stopReason``, so
    emitting it as an update as well would report the end of the turn twice.
    """
    if isinstance(event, TextDelta):
        kind = "agent_thought_chunk" if event.thinking else "agent_message_chunk"
        return {"sessionUpdate": kind, "content": {"type": "text", "text": event.text}}
    if isinstance(event, ToolStart):
        return {
            "sessionUpdate": "tool_call",
            "toolCallId": event.tool_use_id,
            "title": event.name,
            "status": "pending",
            "rawInput": dict(event.arguments),
        }
    if isinstance(event, ToolEnd):
        result = event.result
        return {
            "sessionUpdate": "tool_call_update",
            "toolCallId": event.tool_use_id,
            "status": "completed" if result.ok else "failed",
            "content": [
                {
                    "type": "content",
                    "content": {"type": "text", "text": result.content or result.error},
                }
            ],
        }
    if isinstance(event, Error):
        # No dedicated ACP update for a loop error, so it rides in as agent prose —
        # tagged with its kind so the reader can tell it apart from the model's answer.
        return {
            "sessionUpdate": "agent_message_chunk",
            "content": {"type": "text", "text": f"[{event.kind} error] {event.message}"},
        }
    return None


class AcpServer:
    """Dispatch for one ACP session. Pure request-in, response-out.

    Holds no stream: :func:`serve_acp` does the framing and injects the notification
    sink through :meth:`bind`. That split is what lets the whole method table, the
    handshake gate and the error codes be tested by calling :meth:`handle` with a
    dataclass — and it mirrors :class:`ronin.mcp.server.McpServer` deliberately, since
    the two are the same shape of thing over the same transport.
    """

    def __init__(
        self,
        *,
        open_agent: AgentFactory,
        notify: Notifier | None = None,
        name: str = SERVER_NAME,
        version: str = DEFAULT_SERVER_VERSION,
    ) -> None:
        self._open_agent = open_agent
        self._notify = notify
        self._name = name
        self._version = version
        self._sessions: dict[str, Agent] = {}
        self._initialized = False
        # A counter, not a random id or a timestamp: both are banned, and a session
        # id only has to be opaque and unique within this process, which a counter is.
        self._counter = 0

    @property
    def initialized(self) -> bool:
        return self._initialized

    def bind(self, notify: Notifier) -> None:
        """Wire the notification sink the framing loop owns.

        Called once by :func:`serve_acp` before the read loop starts. A dispatch-only
        test that never runs a prompt can leave it unbound; a test that wants to see
        ``session/update`` frames binds its own collector.
        """
        self._notify = notify

    # --------------------------------------------------------------- methods --

    async def handle(self, request: Request) -> Response | None:
        """One request in, one response out — or ``None`` for a notification.

        Answering a notification is a protocol violation, so the ``None`` is
        load-bearing. A handler that raises :class:`~ronin.mcp.jsonrpc.JsonRpcError`
        becomes that error's response; any other exception becomes ``-32603`` rather
        than killing the connection — a client's bad frame must not end the session.
        """
        try:
            result = await self._dispatch(request)
        except JsonRpcError as exc:
            if request.is_notification:
                return None
            return error_response(request.id, exc)
        except Exception as exc:  # a handler bug must not kill the connection
            if request.is_notification:
                return None
            return error_response(
                request.id,
                JsonRpcError(
                    INTERNAL_ERROR,
                    f"{self._name} failed handling {request.method!r}: {type(exc).__name__}: {exc}",
                ),
            )
        if request.is_notification:
            return None
        return Response(id=request.id, result=result if result is not None else {})

    async def _dispatch(self, request: Request) -> Mapping[str, Any] | None:
        method = request.method
        if method == "initialize":
            return self._initialize(request.params)
        if not self._initialized:
            raise JsonRpcError(
                INVALID_REQUEST,
                f"{method!r} was called before the initialize handshake; an ACP client "
                "sends 'initialize' first and waits for the agent's reply",
            )
        if method == "session/new":
            return await self._session_new(request.params)
        if method == "session/prompt":
            return await self._session_prompt(request.params)
        if method == "session/cancel":
            self._session_cancel(request.params)
            return None
        raise JsonRpcError(
            METHOD_NOT_FOUND,
            f"{self._name} does not implement {method!r}; it implements {sorted(_IMPLEMENTED)}",
        )

    def _initialize(self, params: Mapping[str, Any]) -> Mapping[str, Any]:
        """Answer the handshake, echoing a version we both speak.

        Only text prompts are supported and there is no session persistence, so the
        capabilities advertised are honest about both. ``authMethods`` is empty: a
        stdio agent's client is whoever launched the process, and there is nothing to
        authenticate.
        """
        requested = params.get("protocolVersion")
        version = (
            requested
            if isinstance(requested, int)
            and not isinstance(requested, bool)
            and requested == PROTOCOL_VERSION
            else PROTOCOL_VERSION
        )
        self._initialized = True
        return {
            "protocolVersion": version,
            "agentCapabilities": {
                "promptCapabilities": {
                    "image": False,
                    "audio": False,
                    "embeddedContext": False,
                },
                "loadSession": False,
            },
            "authMethods": [],
        }

    async def _session_new(self, params: Mapping[str, Any]) -> Mapping[str, Any]:
        """Assemble an Agent for ``cwd`` and store it under a fresh session id.

        ``mcpServers`` in the params is accepted and ignored: which servers a session
        connects to is decided by the workspace the factory opens, not dictated over
        the wire, and silently honouring a client's list would be a capability the
        trust model never granted.
        """
        cwd = params.get("cwd")
        if not isinstance(cwd, str) or not cwd:
            raise JsonRpcError(
                INVALID_PARAMS, f"session/new needs a non-empty string 'cwd', got {cwd!r}"
            )
        agent = await self._open_agent(cwd)
        self._counter += 1
        session_id = f"acp-{self._counter}"
        self._sessions[session_id] = agent
        return {"sessionId": session_id}

    async def _session_prompt(self, params: Mapping[str, Any]) -> Mapping[str, Any]:
        """Run one prompt, pushing an update per event, then report the stop reason.

        The prompt's content blocks are flattened to their text — the only kind this
        agent accepts — and fed to :meth:`Agent.run`. The final ``stopReason`` is taken
        from the *last* ``TurnEnd`` seen, because a verify-and-repair pass runs its own
        turns and each ends with one; the last is the one the whole prompt settled on.
        """
        session_id, agent = self._require_session(params)
        text = _prompt_text(params.get("prompt"))
        stop_reason = ""
        async for event in agent.run(text):
            update = event_to_update(event)
            if update is not None:
                await self._emit(session_id, update)
            if isinstance(event, TurnEnd):
                stop_reason = event.stop_reason
        return {"stopReason": stop_reason_to_acp(stop_reason)}

    def _session_cancel(self, params: Mapping[str, Any]) -> None:
        """Ask a running turn to stop. A notification, so best-effort by definition.

        An unknown session id is a no-op rather than an error: the turn the client
        meant to stop is already gone, and there is nobody to send an error to for a
        notification anyway. A known session is cancelled through the policy engine,
        which the loop polls at each iteration boundary.
        """
        session_id = params.get("sessionId")
        if not isinstance(session_id, str):
            return
        agent = self._sessions.get(session_id)
        if agent is not None:
            agent.runtime.policy.cancel()

    def _require_session(self, params: Mapping[str, Any]) -> tuple[str, Agent]:
        session_id = params.get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            raise JsonRpcError(
                INVALID_PARAMS,
                f"a non-empty string 'sessionId' is required, got {session_id!r}",
            )
        agent = self._sessions.get(session_id)
        if agent is None:
            raise JsonRpcError(
                INVALID_PARAMS,
                f"no session called {session_id!r}; call session/new first",
            )
        return session_id, agent

    async def _emit(self, session_id: str, update: Mapping[str, Any]) -> None:
        """Push one ``session/update`` notification, if a sink is bound."""
        if self._notify is None:
            return
        await self._notify(
            Request(
                method="session/update",
                params={"sessionId": session_id, "update": dict(update)},
            )
        )

    async def aclose(self) -> None:
        """Close every session's Agent. Idempotent.

        Each Agent owns shells, MCP clients and a transcript; a served session that
        ended leaves none of them open. Every close is attempted even if one raises —
        :meth:`Agent.aclose` is itself robust, but the loop must not stop at the first
        failure and leak the rest.
        """
        agents = list(self._sessions.values())
        self._sessions.clear()
        for agent in agents:
            await agent.aclose()


_IMPLEMENTED = frozenset({"initialize", "session/new", "session/prompt", "session/cancel"})


def _prompt_text(blocks: object) -> str:
    """The text of an ACP prompt: every ``{type:"text", text:...}`` block, concatenated.

    Blocks of other kinds are dropped rather than rejected — a client may send content
    this agent does not consume, and refusing the whole prompt over one image block is
    worse than answering the text it did send.
    """
    if not isinstance(blocks, Sequence) or isinstance(blocks, (str, bytes)):
        raise JsonRpcError(
            INVALID_PARAMS,
            "session/prompt needs a 'prompt' array of content blocks",
        )
    parts: list[str] = []
    for block in blocks:
        if isinstance(block, Mapping) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)


async def serve_acp(
    server: AcpServer,
    streams: StreamPair,
    *,
    max_frame_bytes: int = MAX_FRAME_BYTES,
) -> None:
    """Read frames from ``streams`` until EOF, answering each and pushing updates.

    Mirrors :func:`ronin.mcp.server.serve_stdio`, with the one difference ACP forces:
    the server also *sends* — so a ``send`` closure is bound as the notification sink
    and shared with the reply path, and every write goes through one lock so a
    ``session/update`` can never interleave mid-line with a response.

    Each frame is handled in its own task rather than inline. That is what lets a
    ``session/cancel`` be read and acted on while the ``session/prompt`` it cancels is
    still streaming; a purely sequential loop could never receive it. On EOF the
    in-flight handlers are cancelled — the peer that would read their replies has gone
    — and every session's Agent is closed.
    """
    lock = asyncio.Lock()
    closed = False

    async def send(message: Request | Response) -> None:
        nonlocal closed
        if closed:
            return
        async with lock:
            try:
                streams.writer.write(encode(message))
                await streams.writer.drain()
            except (OSError, TransportClosed, RuntimeError):
                closed = True

    server.bind(send)
    pending: set[asyncio.Task[None]] = set()

    async def run(frame: bytes) -> None:
        try:
            request = parse_request(frame)
        except JsonRpcError as exc:
            await send(error_response(None, exc))
            return
        response = await server.handle(request)
        if response is not None:
            await send(response)

    try:
        while True:
            try:
                frame = await read_frame(streams.reader, max_bytes=max_frame_bytes)
            except TransportClosed:
                return
            except ProtocolError as exc:
                # No resync point exists after an over-long line, so this is the last
                # thing the connection says — but saying it tells the client the limit.
                await send(error_response(None, JsonRpcError(INVALID_REQUEST, str(exc))))
                return
            task = asyncio.create_task(run(frame))
            pending.add(task)
            task.add_done_callback(pending.discard)
    finally:
        for task in list(pending):
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        await server.aclose()


__all__ = [
    "DEFAULT_SERVER_VERSION",
    "PROTOCOL_VERSION",
    "SERVER_NAME",
    "AcpServer",
    "AgentFactory",
    "Notifier",
    "event_to_update",
    "serve_acp",
    "stop_reason_to_acp",
]
