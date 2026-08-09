"""Ronin as an MCP server: its own tools, and itself, exposed to another agent.

Claude Desktop, Cursor, or a second Ronin can drive this one. Five local tools
(``read``, ``grep``, ``glob``, ``edit``, ``bash``) plus ``ronin_task``, which runs
a full nested agent loop and returns its summary — the tool that makes Ronin
composable with itself. The nested runner is **injected**, so this module knows
nothing about ``ronin.session`` or ``ronin.providers``; the orchestrator supplies
the real one.

TRUST MODEL — read this before enabling the server anywhere
===========================================================

What it protects
----------------
* **The gate travels.** Every tool's ``DangerLevel`` and ``requires_approval``
  are published in ``tools/list`` (as MCP ``annotations`` and a ``_meta`` block),
  and a tool that requires approval **cannot run without a decision**. With no
  :data:`Approver` injected, calling one returns ``-32002`` and does nothing. That
  is the fail-closed default: exposing Ronin over MCP does not silently convert a
  gated tool into an un-gated one.
* **The path boundary travels.** Tools resolve paths through the injected
  ``ToolContext``, so ``read``/``edit``/``glob``/``grep`` refuse to leave the
  context's root exactly as they do locally, and ``edit`` still refuses a file
  that has not been read in this session.
* **Malformed input cannot crash the loop.** Every frame produces a JSON-RPC
  response or, for a notification, nothing. Handler exceptions become ``-32603``.

What it does **not** protect
----------------------------
* **There is no authentication and no authorization.** Anyone who can write to
  this server's stdin is the client, with whatever the ``Approver`` grants. Over
  stdio that is whoever started the process; over a network transport it would be
  whoever can reach the socket, which is why no network listener ships here.
* **An approving ``Approver`` is full trust.** ``bash`` is ``DESTRUCTIVE`` and
  ``ronin_task`` runs an entire agent. An ``Approver`` that returns "approved"
  unconditionally hands the remote client arbitrary code execution as this user.
  It is a legitimate configuration for an unattended runner in a sandbox; it is
  not a safe default, and this module will not supply one.
* **The context root is not a sandbox.** It stops ``../../etc/passwd``; it does
  not stop ``bash`` from walking out. Real containment is a container or a
  seccomp/jail layer around the process, which is somebody else's file.
* **Nothing is audited here.** The server records no history. Whatever the
  orchestrator wires into the ``Approver`` is the audit trail.

In short: this is a *capability handoff*, gated by the ``Approver`` and bounded by
the ``ToolContext``, over a transport with no identity of its own.
"""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, ClassVar

from ..core.types import (
    DENY_UNATTENDED,
    ApprovalDecision,
    ApprovalRequest,
    DangerLevel,
    ToolResult,
    ToolUse,
)
from ..tools.base import Example, Tool, ToolContext
from ..tools.registry import ToolRegistry
from .client import PROTOCOL_VERSION, SUPPORTED_PROTOCOL_VERSIONS
from .jsonrpc import (
    APPROVAL_REQUIRED,
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    TOOL_EXECUTION_FAILED,
    JsonRpcError,
    ProtocolError,
    Request,
    Response,
    TransportClosed,
    encode,
    error_response,
    parse_request,
)
from .transport import MAX_FRAME_BYTES, StreamPair, read_frame

SERVER_NAME = "ronin"

#: Ronin's version is a property of the distribution, not of this module. The
#: orchestrator passes the real one; inventing a number here would be a lie in a
#: field a client may use to decide what it can rely on.
DEFAULT_SERVER_VERSION = "unknown"

#: The local tools worth exposing: enough to read a repo, search it, change one
#: file, and run a command. Deliberately not ``write`` or ``multi_edit`` — a
#: remote client that needs a whole-file overwrite can use ``ronin_task``, which
#: goes through the full loop with its own read-before-write guard.
DEFAULT_EXPOSED_TOOLS: tuple[str, ...] = ("read", "grep", "glob", "edit", "bash")

RONIN_TASK = "ronin_task"

#: Runs a nested agent loop on a prompt and returns its summary. Injected, which
#: is the whole reason this module can live under ``mcp/`` without importing the
#: session or a provider.
NestedRunner = Callable[[str], Awaitable[str]]

#: Asked before any gated tool runs. ``None`` means nobody is attached, and the
#: answer is :data:`ronin.core.types.DENY_UNATTENDED`.
Approver = Callable[[ApprovalRequest], Awaitable[ApprovalDecision]]


class RoninTaskTool(Tool):
    """One prompt in, one summary out, with a whole agent loop in between.

    ``DESTRUCTIVE`` and gated because it is a superset of every tool the nested
    loop has: describing it as anything milder would understate it by definition.
    """

    name = RONIN_TASK
    summary = "Run a full Ronin agent loop on a prompt and return its summary."
    when_to_use = (
        "When the caller wants a whole task done rather than one file read — "
        "'find and fix the failing test', 'summarise how auth works here'. The "
        "nested agent does its own planning, searching and editing, and only the "
        "summary crosses back."
    )
    when_not_to_use = (
        "For anything a single tool answers: one read, one grep, one command. A "
        "nested loop costs a full agent's worth of tokens and its intermediate "
        "steps are invisible to the caller, so a question with a one-call answer "
        "should be asked with that one call."
    )
    schema: ClassVar[Mapping[str, Any]] = {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "The task, stated as you would state it to a colleague.",
            }
        },
        "required": ["prompt"],
        "additionalProperties": False,
    }
    danger_level = DangerLevel.DESTRUCTIVE
    requires_approval = True
    examples: ClassVar[Sequence[Example]] = (
        Example(
            call='ronin_task(prompt="Why does test_login fail on Windows?")',
            result="a summary naming the file and line, and what it changed if anything",
        ),
    )

    def __init__(self, runner: NestedRunner) -> None:
        self._runner = runner

    async def run(self, args: Mapping[str, Any], ctx: ToolContext) -> ToolResult:
        prompt = args.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            return ToolResult(
                ok=False,
                error="'prompt' is required and must be a non-empty string describing the task.",
            )
        summary = await self._runner(prompt)
        return ToolResult(ok=True, content=summary)


class McpServer:
    """Dispatch for one MCP session. Pure request-in, response-out.

    Holds no stream and no socket: :func:`serve_stdio` does the framing. That
    split is what lets the whole method table, the error codes and the approval
    gate be tested by calling :meth:`handle` with a dataclass.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        nested_runner: NestedRunner | None = None,
        exposed: Sequence[str] = DEFAULT_EXPOSED_TOOLS,
        approver: Approver | None = None,
        name: str = SERVER_NAME,
        version: str = DEFAULT_SERVER_VERSION,
    ) -> None:
        tools: list[Tool] = []
        missing: list[str] = []
        for tool_name in exposed:
            tool = registry.tool(tool_name)
            if tool is None:
                # A registry built without a shell has no `bash`. That is the
                # documented way to configure a restricted session, so it is a
                # recorded absence rather than a startup failure.
                missing.append(tool_name)
                continue
            tools.append(tool)
        if nested_runner is not None:
            tools.append(RoninTaskTool(nested_runner))
        self._registry = ToolRegistry(tools, registry.ctx)
        self._missing = tuple(missing)
        self._approver = approver
        self._name = name
        self._version = version
        self._initialized = False
        self._calls = 0

    @property
    def unavailable_tools(self) -> tuple[str, ...]:
        """Requested names the registry did not have. For a startup banner."""
        return self._missing

    @property
    def initialized(self) -> bool:
        return self._initialized

    # --------------------------------------------------------------- methods --

    async def handle(self, request: Request) -> Response | None:
        """One request in, one response out — or ``None`` for a notification.

        Answering a notification is a protocol violation that some clients treat
        as a stray response and drop the connection over, so the ``None`` is
        load-bearing rather than an optimisation.
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
                    f"{self._name} failed handling {request.method!r}: "
                    f"{type(exc).__name__}: {exc}",
                ),
            )
        if request.is_notification:
            return None
        return Response(id=request.id, result=result if result is not None else {})

    async def _dispatch(self, request: Request) -> Mapping[str, Any] | None:
        method = request.method
        if method == "initialize":
            return self._initialize(request.params)
        if method == "notifications/initialized":
            self._initialized = True
            return None
        if method == "ping":
            return {}
        if not self._initialized:
            raise JsonRpcError(
                INVALID_REQUEST,
                f"{method!r} was called before the initialize handshake completed; "
                "send 'initialize', then the 'notifications/initialized' notification",
            )
        if method == "tools/list":
            return {"tools": self.tool_descriptors()}
        if method == "tools/call":
            return await self._call(request.params)
        if method.startswith("notifications/"):
            # An unknown notification is ignored on purpose: the spec allows a
            # peer to send ones we have never heard of, and -32601 for a message
            # that expects no answer would be a response to a notification.
            return None
        raise JsonRpcError(
            METHOD_NOT_FOUND,
            f"{self._name} does not implement {method!r}; it implements "
            f"{sorted(_IMPLEMENTED)}",
        )

    def _initialize(self, params: Mapping[str, Any]) -> Mapping[str, Any]:
        """Answer the handshake, echoing a version we both speak.

        A client that asked for something unknown gets *our* version rather than
        an error, which is what the spec prescribes: the client then decides
        whether it can live with it. Erroring here would break clients that are
        merely newer than us.
        """
        requested = params.get("protocolVersion")
        version = (
            requested
            if isinstance(requested, str) and requested in SUPPORTED_PROTOCOL_VERSIONS
            else PROTOCOL_VERSION
        )
        # Some clients never send `notifications/initialized`. Treating the
        # `initialize` call itself as sufficient avoids a session that answers
        # -32600 to everything, which looks like a broken server.
        self._initialized = True
        return {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": self._name, "version": self._version},
        }

    def tool_descriptors(self) -> list[dict[str, Any]]:
        """``tools/list``'s payload, with the danger level carried across.

        The hints are MCP's own vocabulary so any client can read them; ``_meta``
        carries the exact ``DangerLevel`` name and the approval flag, because
        "destructiveHint: true" loses the difference between ``DESTRUCTIVE`` and
        ``IRREVERSIBLE`` and a client that wants to prompt differently needs it.
        """
        descriptors: list[dict[str, Any]] = []
        for spec in self._registry.specs():
            level = spec.danger_level
            descriptors.append(
                {
                    "name": spec.name,
                    "description": spec.description,
                    "inputSchema": dict(spec.json_schema) or _EMPTY_SCHEMA,
                    "annotations": {
                        "title": spec.name,
                        "readOnlyHint": level is DangerLevel.READ_ONLY,
                        "destructiveHint": level >= DangerLevel.DESTRUCTIVE,
                        "idempotentHint": False,
                        "openWorldHint": False,
                    },
                    "_meta": {
                        "ronin/dangerLevel": level.name.lower(),
                        "ronin/requiresApproval": spec.requires_approval,
                        "ronin/approverAttached": self._approver is not None,
                    },
                }
            )
        return descriptors

    async def _call(self, params: Mapping[str, Any]) -> Mapping[str, Any]:
        name = params.get("name")
        if not isinstance(name, str) or not name:
            raise JsonRpcError(
                INVALID_PARAMS,
                f"tools/call needs a non-empty string 'name', got {name!r}",
            )
        arguments = params.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, Mapping):
            raise JsonRpcError(
                INVALID_PARAMS,
                f"tools/call 'arguments' must be an object, got "
                f"{type(arguments).__name__}",
            )
        spec = self._registry.get(name)
        if spec is None:
            # MCP specifies invalid-params for an unknown tool, not a
            # server-defined code: the request named something that does not
            # exist, which is the caller's bug and never worth a retry.
            raise JsonRpcError(
                INVALID_PARAMS,
                f"{self._name} exposes no tool called {name!r}; it exposes "
                f"{[s.name for s in self._registry.specs()]}",
            )
        if spec.requires_approval:
            decision = await self._decide(name, spec.danger_level, arguments)
            if not decision.approved:
                raise JsonRpcError(
                    APPROVAL_REQUIRED,
                    f"{name} is {spec.danger_level.name} and requires approval; "
                    f"the request was denied: {decision.reason or 'no reason given'}",
                    {"tool": name, "dangerLevel": spec.danger_level.name.lower()},
                )
        self._calls += 1
        result = await self._registry.execute(
            ToolUse(id=f"mcp-{self._calls}", name=name, arguments=arguments)
        )
        if not result.ok:
            # The server-defined range, not -32603: the tool ran and refused, so
            # the fault is not the server's and the same call will fail the same
            # way. A client that read this as "internal error" would retry it.
            raise JsonRpcError(
                TOOL_EXECUTION_FAILED,
                result.error,
                {"tool": name, "isError": True},
            )
        return {
            "content": [{"type": "text", "text": result.content}],
            "isError": False,
        }

    async def _decide(
        self, name: str, level: DangerLevel, arguments: Mapping[str, Any]
    ) -> ApprovalDecision:
        if self._approver is None:
            return DENY_UNATTENDED
        return await self._approver(
            ApprovalRequest(
                tool_use_id=f"mcp-{self._calls + 1}",
                name=name,
                danger_level=level,
                rendered=render_call(name, arguments),
            )
        )

    # ---------------------------------------------------------------- framing --

    async def handle_frame(self, raw: bytes | str) -> bytes | None:
        """One wire frame in, one encoded frame out (or ``None``).

        The only place a parse failure is turned into ``-32700``. The id is
        ``null`` because a frame we could not parse has no id to echo — which is
        the one case where JSON-RPC's null id is correct.
        """
        try:
            request = parse_request(raw)
        except JsonRpcError as exc:
            return encode(error_response(None, exc))
        response = await self.handle(request)
        return None if response is None else encode(response)


_IMPLEMENTED = frozenset(
    {"initialize", "notifications/initialized", "ping", "tools/list", "tools/call"}
)

_EMPTY_SCHEMA: Mapping[str, Any] = {"type": "object", "properties": {}}


def render_call(name: str, arguments: Mapping[str, Any]) -> str:
    """What a human approving this call is shown. What is shown is what runs.

    Deliberately the *whole* argument mapping, compact but untruncated: an
    approval prompt that elides the tail of a command is an approval of something
    the human did not read.
    """
    body = json.dumps(dict(arguments), separators=(", ", ": "), ensure_ascii=False, default=str)
    return f"{name} {body}"


async def serve_stdio(
    server: McpServer,
    streams: StreamPair,
    *,
    max_frame_bytes: int = MAX_FRAME_BYTES,
) -> None:
    """Read requests from ``streams`` until EOF, answering each in turn.

    Takes an injected :class:`~ronin.mcp.transport.StreamPair` rather than
    touching ``sys.stdin``: that is what lets the tests and the demo drive a real
    server with no subprocess, and a caller wanting the real thing passes the
    pipes it already has.

    Requests are handled **sequentially**. MCP permits concurrency, but the tools
    below share one ``ToolContext`` and one persistent shell, and two ``bash``
    calls interleaved in one shell is a correctness bug rather than a speedup.
    """
    while True:
        try:
            frame = await read_frame(streams.reader, max_bytes=max_frame_bytes)
        except TransportClosed:
            return
        except ProtocolError as exc:
            # No resync point exists after an over-long line, so this is the last
            # thing the connection says. Saying it is still worth more than a
            # silent hang-up: the client learns the limit it exceeded.
            await _write(
                streams,
                encode(error_response(None, JsonRpcError(INVALID_REQUEST, str(exc)))),
            )
            return
        reply = await server.handle_frame(frame)
        if reply is not None and not await _write(streams, reply):
            return


async def _write(streams: StreamPair, payload: bytes) -> bool:
    """Write one frame. ``False`` once the peer has gone."""
    try:
        streams.writer.write(payload)
        await streams.writer.drain()
    except (OSError, TransportClosed, RuntimeError):
        return False
    return True


__all__ = [
    "DEFAULT_EXPOSED_TOOLS",
    "DEFAULT_SERVER_VERSION",
    "RONIN_TASK",
    "SERVER_NAME",
    "Approver",
    "McpServer",
    "NestedRunner",
    "RoninTaskTool",
    "render_call",
    "serve_stdio",
]
