"""The client: MCP servers become tools, and a dead server does not end the session.

The handshake, the capability record, and the tool wrapping are the easy parts.
The requirement that shapes this module is the last one: **an MCP server is a
separate process that will die mid-conversation**, and when it does, the agent's
turn must continue. So:

* a server that fails the handshake, or speaks a protocol version we do not
  understand, or reports no ``tools`` capability, **contributes no tools** and is
  recorded in :attr:`McpToolset.failures`. It never raises past
  :func:`connect_all`, because one broken entry in ``mcp.json`` must not cost the
  session its local tools;
* a server that dies *after* its tools are in the registry keeps them there, and
  each one returns ``ToolResult(ok=False)`` with a message written for the model
  to act on — "these tools are gone, use local ones or tell the user" — rather
  than an exception that unwinds the loop;
* reconnection is attempted, once by default, and *counted*. An unbounded
  reconnect against a server that crashes on startup is an infinite loop with a
  subprocess spawn in it.

Nothing here imports ``ronin.providers``: the transports take an injected sender,
so the only thing this module knows about the network is that someone else does it.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..core.types import ToolResult
from .config import ConfigError, McpServerConfig, TransportKind, enabled_servers
from .jsonrpc import (
    JsonRpcError,
    McpError,
    ProtocolError,
    Request,
    Response,
    TransportClosed,
)
from .tools import RemoteTool, remote_tools
from .transport import (
    HttpSender,
    HttpTransport,
    SseTransport,
    StdioTransport,
    Transport,
)

#: The version this client asks for. MCP negotiates by the client proposing one
#: and the server answering with the version it will actually use.
PROTOCOL_VERSION = "2025-06-18"

#: Versions whose ``initialize`` / ``tools/list`` / ``tools/call`` shapes this
#: implementation handles. A server answering with anything else contributes no
#: tools: guessing at an unknown revision's payload shape is how a client starts
#: silently dropping fields.
SUPPORTED_PROTOCOL_VERSIONS: frozenset[str] = frozenset(
    {"2024-11-05", "2025-03-26", "2025-06-18"}
)

CLIENT_NAME = "ronin"

#: Ronin's own version is set by the distribution, which this package cannot see
#: without importing packaging metadata. The orchestrator passes the real one.
CLIENT_VERSION_UNKNOWN = "unknown"

#: Reconnect attempts per server, per session. One is enough to survive a server
#: that was restarted; more only prolongs an outage.
DEFAULT_MAX_RECONNECTS = 1

#: Ceiling on ``tools/list`` pagination. A server that returns a cursor forever
#: would otherwise pin the CPU during startup.
MAX_TOOL_LIST_PAGES = 50

#: How a client obtains a *fresh* transport. Called once per connection attempt,
#: never reused: a half-read stream looks identical to a healthy one, and reusing
#: it is how a "reconnect" quietly keeps failing.
TransportProvider = Callable[[McpServerConfig], Transport]


@dataclass(frozen=True, slots=True)
class ServerCapabilities:
    """What one server said it can do, recorded rather than assumed.

    ``supports_tools`` is the only field the tool path consults, and it is read
    from the server's own ``capabilities`` object. A server with resources and
    prompts but no tools is a perfectly valid MCP server; it simply contributes
    nothing here, and saying so is better than calling ``tools/list`` and treating
    the ``-32601`` as a failure.
    """

    protocol_version: str
    server_name: str
    server_version: str
    supports_tools: bool = False
    supports_resources: bool = False
    supports_prompts: bool = False
    supports_logging: bool = False
    raw: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_initialize(cls, result: Mapping[str, Any]) -> ServerCapabilities:
        capabilities = result.get("capabilities")
        capabilities = capabilities if isinstance(capabilities, Mapping) else {}
        info = result.get("serverInfo")
        info = info if isinstance(info, Mapping) else {}
        return cls(
            protocol_version=str(result.get("protocolVersion", "")),
            server_name=str(info.get("name", "")),
            server_version=str(info.get("version", "")),
            supports_tools="tools" in capabilities,
            supports_resources="resources" in capabilities,
            supports_prompts="prompts" in capabilities,
            supports_logging="logging" in capabilities,
            raw=dict(capabilities),
        )


class McpClient:
    """One server, over one transport, with a bounded reconnect budget.

    Mutable because a connection is: it has a live transport, a death, and a
    reconnect count. The mutation is confined to this object, and every state
    change goes through :meth:`_die` or :meth:`connect`.
    """

    def __init__(
        self,
        config: McpServerConfig,
        provider: TransportProvider,
        *,
        max_reconnects: int = DEFAULT_MAX_RECONNECTS,
        client_version: str = CLIENT_VERSION_UNKNOWN,
    ) -> None:
        self.config = config
        self._provider = provider
        self._max_reconnects = max(0, max_reconnects)
        self._client_version = client_version
        self._transport: Transport | None = None
        self._capabilities: ServerCapabilities | None = None
        self._descriptors: tuple[Mapping[str, Any], ...] = ()
        self._reconnects_used = 0
        self._dead_reason = ""
        self._next_id = 0

    # ---------------------------------------------------------------- state --

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def capabilities(self) -> ServerCapabilities | None:
        return self._capabilities

    @property
    def alive(self) -> bool:
        transport = self._transport
        return transport is not None and transport.alive and not self._dead_reason

    @property
    def dead_reason(self) -> str:
        return self._dead_reason

    @property
    def reconnects_used(self) -> int:
        return self._reconnects_used

    # ------------------------------------------------------------ handshake --

    async def connect(self) -> ServerCapabilities:
        """Open a transport and run ``initialize``. Raises on any failure.

        Raising is right at this level and wrong one level up: the caller that
        knows how to degrade is :func:`connect_all`, and a client that swallowed
        its own handshake failure would leave nothing to report.
        """
        transport = self._provider(self.config)
        await transport.start()
        self._transport = transport
        self._dead_reason = ""
        response = await self._send(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                # Ronin exposes no client-side capabilities: it does not serve
                # sampling, roots, or elicitation back to the server. Declaring
                # none is what makes ignoring a server-initiated request correct.
                "capabilities": {},
                "clientInfo": {"name": CLIENT_NAME, "version": self._client_version},
            },
        )
        result = self._result_or_raise(response, "initialize")
        capabilities = ServerCapabilities.from_initialize(result)
        if capabilities.protocol_version not in SUPPORTED_PROTOCOL_VERSIONS:
            self._die(
                f"server {self.name!r} answered with protocol version "
                f"{capabilities.protocol_version!r}; this client speaks "
                f"{sorted(SUPPORTED_PROTOCOL_VERSIONS)}"
            )
            raise ProtocolError(self._dead_reason)
        self._capabilities = capabilities
        await self._notify("notifications/initialized")
        return capabilities

    async def list_tools(self) -> tuple[Mapping[str, Any], ...]:
        """Every tool descriptor the server offers, following ``nextCursor``.

        Returns empty for a server that declared no ``tools`` capability, without
        sending anything. That is the "a server that does not support tools
        contributes none rather than erroring" rule, and it lives here so no
        caller has to remember it.
        """
        capabilities = self._capabilities
        if capabilities is None:
            raise McpError(f"server {self.name!r}: list_tools before connect")
        if not capabilities.supports_tools:
            self._descriptors = ()
            return ()
        collected: list[Mapping[str, Any]] = []
        cursor: str | None = None
        for _ in range(MAX_TOOL_LIST_PAGES):
            params: dict[str, Any] = {"cursor": cursor} if cursor else {}
            result = self._result_or_raise(await self._send("tools/list", params), "tools/list")
            page = result.get("tools")
            if not isinstance(page, Sequence) or isinstance(page, (str, bytes)):
                raise ProtocolError(
                    f"server {self.name!r}: tools/list returned "
                    f"{type(page).__name__} for 'tools', expected a list"
                )
            collected.extend(item for item in page if isinstance(item, Mapping))
            raw_cursor = result.get("nextCursor")
            if not isinstance(raw_cursor, str) or not raw_cursor:
                break
            cursor = raw_cursor
        else:
            raise ProtocolError(
                f"server {self.name!r}: tools/list paginated past "
                f"{MAX_TOOL_LIST_PAGES} pages; refusing to keep asking"
            )
        self._descriptors = tuple(collected)
        return self._descriptors

    def build_tools(self) -> tuple[tuple[RemoteTool, ...], tuple[str, ...]]:
        """``(tools, skipped)`` from the last :meth:`list_tools` reply."""
        return remote_tools(self.config, self._descriptors, self.call_tool)

    # --------------------------------------------------------------- calling --

    async def call_tool(self, name: str, arguments: Mapping[str, Any]) -> ToolResult:
        """Invoke one remote tool. **Never raises** — this is a tool boundary.

        Three failure shapes collapse into ``ToolResult(ok=False)``: the transport
        died, the server answered with a JSON-RPC ``error``, and the server
        answered with ``isError: true``. The model needs the same recovery in all
        three cases, and the error string tells it what that is.
        """
        try:
            response = await self._send_with_recovery(
                "tools/call", {"name": name, "arguments": dict(arguments)}
            )
        except McpError:
            return ToolResult(ok=False, error=self._unavailable_message())
        if response.error is not None:
            return ToolResult(
                ok=False,
                error=(
                    f"{self.name}/{name} refused: "
                    f"{response.error.message or response.error.describe()} "
                    f"(JSON-RPC code {response.error.code}). This is the server's "
                    "answer, not a transport failure — the same call will fail the "
                    "same way, so fix the arguments or do something else."
                ),
            )
        result = response.result or {}
        content = render_content(result.get("content"))
        if result.get("isError") is True:
            return ToolResult(
                ok=False,
                error=content
                or f"{self.name}/{name} reported failure without saying why.",
            )
        structured = result.get("structuredContent")
        if not content and isinstance(structured, Mapping):
            content = _compact_json(structured)
        return ToolResult(ok=True, content=content)

    async def close(self) -> None:
        transport, self._transport = self._transport, None
        if transport is not None:
            await transport.close()

    # ----------------------------------------------------------- internals --

    async def _send_with_recovery(
        self, method: str, params: Mapping[str, Any]
    ) -> Response:
        """One request, with at most ``max_reconnects`` re-handshakes behind it."""
        try:
            return await self._send(method, params)
        except (TransportClosed, ProtocolError, OSError) as exc:
            self._die(str(exc))
        if self._reconnects_used >= self._max_reconnects:
            raise TransportClosed(self._dead_reason)
        self._reconnects_used += 1
        previous = self._dead_reason
        try:
            await self.close()
            await self.connect()
            await self.list_tools()
            return await self._send(method, params)
        except (McpError, OSError) as exc:
            self._die(f"{previous}; reconnect attempt {self._reconnects_used} failed: {exc}")
            raise TransportClosed(self._dead_reason) from exc

    async def _send(self, method: str, params: Mapping[str, Any]) -> Response:
        transport = self._transport
        if transport is None:
            raise TransportClosed(f"server {self.name!r} has no open transport")
        self._next_id += 1
        return await transport.send(Request(method=method, params=params, id=self._next_id))

    async def _notify(self, method: str, params: Mapping[str, Any] | None = None) -> None:
        transport = self._transport
        if transport is None:
            raise TransportClosed(f"server {self.name!r} has no open transport")
        await transport.notify(Request(method=method, params=params or {}))

    def _result_or_raise(self, response: Response, method: str) -> Mapping[str, Any]:
        if response.error is not None:
            raise JsonRpcError(
                response.error.code,
                f"server {self.name!r} rejected {method!r}: {response.error.message}",
                response.error.data,
            )
        return response.result or {}

    def _die(self, reason: str) -> None:
        if not self._dead_reason:
            self._dead_reason = reason

    def _unavailable_message(self) -> str:
        """The string a dead server's tools hand the model. It is a prompt.

        Says what happened, what is gone, and what to do instead — including "do
        not retry", because a model that retries a dead server burns the turn.
        """
        transport = self._transport
        detail = self._dead_reason or (transport.detail if transport else "")
        attempts = (
            f" {self._reconnects_used} reconnect attempt(s) were made."
            if self._reconnects_used
            else ""
        )
        return (
            f"the {self.name!r} MCP server is not responding: "
            f"{detail or 'the connection ended'}.{attempts} Every "
            f"mcp__{self.name}__* tool is unavailable for the rest of this "
            "session. Do not retry them. Use the local tools instead, or tell the "
            "user that the server is down and what you could not do because of it."
        )


# --------------------------------------------------------------------------- #
# Content
# --------------------------------------------------------------------------- #

#: MCP content blocks Ronin can render as text. Anything else is marked, not
#: dropped — a silently discarded image is a model wondering why the tool said
#: nothing.
_TEXT_TYPES = frozenset({"text"})


def render_content(blocks: object) -> str:
    """Flatten an MCP ``content`` array into the text a model sees.

    Every block a session cannot forward is replaced by a marker naming what was
    cut. Truncation and omission are always visible; that rule is the reason the
    unsupported branches say what they dropped instead of returning "".
    """
    if isinstance(blocks, str):
        return blocks
    if not isinstance(blocks, Sequence):
        return ""
    parts: list[str] = []
    for block in blocks:
        if isinstance(block, str):
            parts.append(block)
            continue
        if not isinstance(block, Mapping):
            parts.append(f"[unrenderable MCP content entry of type {type(block).__name__}]")
            continue
        kind = str(block.get("type", ""))
        if kind in _TEXT_TYPES:
            parts.append(str(block.get("text", "")))
        elif kind in {"image", "audio"}:
            data = block.get("data")
            size = len(data) if isinstance(data, str) else 0
            parts.append(
                f"[{kind} block omitted: {block.get('mimeType', 'unknown type')}, "
                f"{size} base64 chars — MCP {kind} content is not forwarded into "
                "the transcript]"
            )
        elif kind in {"resource", "resource_link"}:
            parts.append(_render_resource(block))
        else:
            parts.append(f"[unsupported MCP content block of type {kind!r}]")
    return "\n".join(part for part in parts if part)


def _render_resource(block: Mapping[str, Any]) -> str:
    resource = block.get("resource")
    resource = resource if isinstance(resource, Mapping) else block
    uri = resource.get("uri", "")
    text = resource.get("text")
    if isinstance(text, str) and text:
        return f"{uri}:\n{text}" if uri else text
    return f"[resource {uri or 'with no uri'} carried no text content]"


def _compact_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, default=str)


# --------------------------------------------------------------------------- #
# Assembling a session's servers
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class McpToolset:
    """Everything a session got from MCP, including what it did not get.

    ``failures`` and ``skipped`` are part of the value, not a log: a session that
    silently has three fewer tools than the config asked for is impossible to
    debug from the outside, and the UI needs something to show.
    """

    tools: tuple[RemoteTool, ...] = ()
    clients: Mapping[str, McpClient] = field(default_factory=dict)
    failures: Mapping[str, str] = field(default_factory=dict)
    skipped: tuple[str, ...] = ()

    def summary(self) -> str:
        """One line per server, for a startup banner or a test assertion."""
        lines = [
            f"{name}: {len([t for t in self.tools if t.server == name])} tool(s)"
            for name in self.clients
        ]
        lines.extend(f"{name}: unavailable — {why}" for name, why in self.failures.items())
        lines.extend(f"skipped {note}" for note in self.skipped)
        return "\n".join(lines) or "no MCP servers configured"

    async def aclose(self) -> None:
        for client in self.clients.values():
            await client.close()


async def connect_all(
    configs: Sequence[McpServerConfig],
    provider: TransportProvider,
    *,
    max_reconnects: int = DEFAULT_MAX_RECONNECTS,
    client_version: str = CLIENT_VERSION_UNKNOWN,
) -> McpToolset:
    """Connect to every enabled server and collect their tools.

    Never raises for a server's sake. A handshake failure, an unsupported protocol
    version, a server with no tools capability, and a tool whose name cannot be
    namespaced are all *recorded* — because the alternative is one bad line in
    ``mcp.json`` taking down a session that would otherwise work fine with its
    local tools.
    """
    clients: dict[str, McpClient] = {}
    failures: dict[str, str] = {}
    tools: list[RemoteTool] = []
    skipped: list[str] = []
    for config in enabled_servers(tuple(configs)):
        client = McpClient(
            config,
            provider,
            max_reconnects=max_reconnects,
            client_version=client_version,
        )
        try:
            capabilities = await client.connect()
            descriptors = await client.list_tools()
        except (McpError, OSError) as exc:
            failures[config.name] = str(exc)
            await client.close()
            continue
        clients[config.name] = client
        if not capabilities.supports_tools:
            failures[config.name] = (
                f"connected, but declared no 'tools' capability "
                f"(offers: {sorted(capabilities.raw) or 'nothing'}); it contributes "
                "no tools and the session continues"
            )
            continue
        if not descriptors:
            failures[config.name] = "connected and supports tools, but listed none"
            continue
        built, dropped = client.build_tools()
        tools.extend(built)
        skipped.extend(dropped)
    return McpToolset(
        tools=tuple(tools),
        clients=clients,
        failures=failures,
        skipped=tuple(skipped),
    )


def default_transport_provider(*, sender: HttpSender | None = None) -> TransportProvider:
    """Build real transports from config: a subprocess for stdio, ``sender`` above.

    ``sender`` is required for ``sse``/``http`` and its absence is a
    :class:`ConfigError` at connection time rather than a silently skipped server,
    because "I configured a remote server and no tools appeared" is exactly the
    diagnosis this package exists to avoid.
    """

    def build(config: McpServerConfig) -> Transport:
        if config.transport is TransportKind.STDIO:
            return StdioTransport(
                command=config.argv,
                env=config.env or None,
                cwd=config.cwd or None,
                timeout=config.timeout_seconds,
            )
        if sender is None:
            raise ConfigError(
                f"server {config.name!r} uses the {config.transport.value!r} "
                "transport, which needs an HTTP sender; none was supplied to "
                "default_transport_provider()"
            )
        if config.transport is TransportKind.SSE:
            return SseTransport(
                url=config.url,
                sender=sender,
                headers=config.headers,
                timeout=config.timeout_seconds,
            )
        return HttpTransport(
            url=config.url,
            sender=sender,
            headers=config.headers,
            timeout=config.timeout_seconds,
        )

    return build


__all__ = [
    "CLIENT_NAME",
    "CLIENT_VERSION_UNKNOWN",
    "DEFAULT_MAX_RECONNECTS",
    "MAX_TOOL_LIST_PAGES",
    "PROTOCOL_VERSION",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "McpClient",
    "McpToolset",
    "ServerCapabilities",
    "TransportProvider",
    "connect_all",
    "default_transport_provider",
    "render_content",
]
