"""See the MCP layer work: ``python -m ronin.mcp.demo``.

Entirely in-process. No subprocess, no socket, no network: the "external" MCP
servers are :class:`FakeServer` objects wired to the client through
``memory_duplex`` streams, and Ronin's own server is the real :class:`McpServer`
driven the same way. Every byte on those streams is real JSON-RPC, framed exactly
as it would be over a pipe.

Six things get demonstrated, and three of them are refusals, because a client's
refusals are the half you cannot see from a passing handshake:

1. the ``initialize`` handshake and what each server said it can do;
2. a server that supports no tools contributing none, without erroring;
3. namespaced tools appearing in a real ``ToolRegistry`` alongside local ones;
4. a call round-tripping through the wrapper as an ordinary tool call;
5. a server dying mid-session — its tools fail with a message the model can act
   on, one reconnect is attempted, and the *other* server keeps working;
6. Ronin's own server answering ``tools/list``, refusing a gated tool with no
   approver attached, and then running ``ronin_task`` once one is.
"""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from ..core.types import ApprovalDecision, ApprovalRequest, ToolResult, ToolUse
from ..tools.base import ToolContext
from ..tools.registry import ToolRegistry, build_registry
from .client import PROTOCOL_VERSION, McpClient, McpToolset, connect_all
from .config import McpServerConfig, TransportKind, parse_mcp_config
from .jsonrpc import (
    INVALID_PARAMS,
    JsonRpcError,
    ProtocolError,
    Request,
    Response,
    TransportClosed,
    encode,
    error_response,
    parse_request,
)
from .server import McpServer, serve_stdio
from .tools import extend_registry
from .transport import StdioTransport, StreamPair, memory_duplex, read_frame

#: A tool handler on the fake server: arguments in, text out.
Handler = Callable[[Mapping[str, Any]], str]


def rule(title: str) -> None:
    print(f"\n{'─' * 78}\n{title}\n{'─' * 78}")


def show(label: str, result: ToolResult) -> None:
    mark = "✔" if result.ok else "✗"
    body = result.content if result.ok else result.error
    print(f"  {mark} {label}")
    for line in _wrap(body):
        print(f"      {line}")


def _wrap(text: str, width: int = 68) -> list[str]:
    out: list[str] = []
    for paragraph in text.split("\n"):
        line = ""
        for word in paragraph.split(" "):
            if line and len(line) + len(word) + 1 > width:
                out.append(line)
                line = word
            else:
                line = f"{line} {word}".strip()
        out.append(line)
    return out


# --------------------------------------------------------------------------- #
# A fake external MCP server, in this process
# --------------------------------------------------------------------------- #


class FakeServer:
    """A minimal MCP server over injected streams. Killable.

    Not a mock of Ronin's own server — a stand-in for somebody else's, so the
    client is exercised against a wire shape it does not control. Small enough to
    double as documentation of the three methods that matter.
    """

    def __init__(
        self,
        name: str,
        *,
        tools: Mapping[str, tuple[str, Handler]] | None = None,
        capabilities: Sequence[str] = ("tools",),
        protocol_version: str = PROTOCOL_VERSION,
    ) -> None:
        self.name = name
        self.tools = dict(tools or {})
        self.capabilities = tuple(capabilities)
        self.protocol_version = protocol_version
        self.dead = False
        self.connections = 0
        self._open: list[tuple[asyncio.Task[None], StreamPair]] = []

    def open(self) -> StreamPair:
        """The client's end of a fresh connection. Already at EOF once killed."""
        self.connections += 1
        near, far = memory_duplex()
        if self.dead:
            far.writer.close()
            return near
        self._open.append((asyncio.create_task(self._serve(far)), far))
        return near

    def kill(self) -> None:
        """Die the way a crashed subprocess dies: EOF on every open pipe.

        Cancelling the serve task alone would leave the client waiting for its
        timeout, which is a *hang*, not a death — a different failure mode with a
        different recovery, and testing one while claiming the other is how a
        degradation path passes its tests and fails in production.
        """
        self.dead = True
        for task, far in self._open:
            far.writer.close()
            task.cancel()
        self._open.clear()

    async def _serve(self, streams: StreamPair) -> None:
        while not self.dead:
            try:
                frame = await read_frame(streams.reader)
            except (TransportClosed, ProtocolError):
                return
            try:
                request = parse_request(frame)
            except JsonRpcError as exc:
                await self._write(streams, encode(error_response(None, exc)))
                continue
            response = self._answer(request)
            if response is not None and not await self._write(streams, encode(response)):
                return
        streams.writer.close()

    def _answer(self, request: Request) -> Response | None:
        if request.is_notification:
            return None
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
        if request.method == "tools/list":
            return {
                "tools": [
                    {
                        "name": tool,
                        "description": description,
                        "inputSchema": {
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                            "required": ["query"],
                        },
                    }
                    for tool, (description, _) in self.tools.items()
                ]
            }
        if request.method == "tools/call":
            name = str(request.params.get("name", ""))
            entry = self.tools.get(name)
            if entry is None:
                raise JsonRpcError(INVALID_PARAMS, f"no tool called {name!r}")
            arguments = request.params.get("arguments")
            text = entry[1](arguments if isinstance(arguments, Mapping) else {})
            return {"content": [{"type": "text", "text": text}], "isError": False}
        raise JsonRpcError(INVALID_PARAMS, f"fake server does not implement {request.method!r}")

    async def _write(self, streams: StreamPair, payload: bytes) -> bool:
        try:
            streams.writer.write(payload)
            await streams.writer.drain()
        except (TransportClosed, OSError, RuntimeError):
            return False
        return True


def _docs_search(args: Mapping[str, Any]) -> str:
    return f"3 results for {args.get('query', '')!r}: ADR-004, ADR-011, RFC-2"


def _tickets_open(args: Mapping[str, Any]) -> str:
    return f"RON-88 open, matching {args.get('query', '')!r}"


CONFIG_JSON: Mapping[str, Any] = {
    "mcpServers": {
        "docs": {
            "transport": "stdio",
            "command": "fake-docs",
            "danger_level": "read_only",
            "requires_approval": False,
        },
        "tickets": {"transport": "stdio", "command": "fake-tickets"},
        "memo": {"transport": "stdio", "command": "fake-memo"},
    }
}


# --------------------------------------------------------------------------- #
# The demo
# --------------------------------------------------------------------------- #


async def demo_handshake(
    configs: Sequence[McpServerConfig],
    fakes: Mapping[str, FakeServer],
    ctx: ToolContext,
) -> tuple[McpToolset, ToolRegistry]:
    rule("1. the handshake, and what each server admitted to")
    print("  Three servers in .ronin/mcp.json. 'memo' answers initialize but declares")
    print("  no tools capability, so it contributes nothing — and the session lives.\n")

    def provider(config: McpServerConfig) -> StdioTransport:
        return StdioTransport(streams=fakes[config.name].open(), timeout=5.0)

    toolset = await connect_all(configs, provider, client_version="demo")
    for name, client in toolset.clients.items():
        capabilities = client.capabilities
        assert capabilities is not None
        print(
            f"  ✔ {name}: protocol {capabilities.protocol_version}, "
            f"server {capabilities.server_name}/{capabilities.server_version}, "
            f"tools={capabilities.supports_tools}"
        )
    for name, why in toolset.failures.items():
        print(f"  ✗ {name}: {why}")

    rule("2. namespaced tools in a real ToolRegistry, next to the local ones")
    registry = extend_registry(build_registry(ctx), toolset.tools)
    names = [spec.name for spec in registry.specs()]
    print(f"  {len(names)} tools: {', '.join(names)}\n")
    print("  Two servers both expose a tool called 'search'. Namespacing makes the")
    print("  collision impossible rather than resolving it:")
    for spec in registry.specs():
        if spec.name.startswith("mcp__"):
            print(
                f"    {spec.name:<26} danger={spec.danger_level.name.lower():<11} "
                f"approval={spec.requires_approval}"
            )
    print()
    print("  'tickets' declared no danger_level, so it fails closed to MUTATING +")
    print("  approval. 'docs' declared read_only AND requires_approval=false — two")
    print("  explicit keys — so it is un-gated.\n")
    described = registry.get("mcp__docs__search")
    assert described is not None
    print("  The description is rewritten into the house format, and the parts the")
    print("  upstream server never supplied say that they are synthesized:\n")
    for line in described.description.split("\n"):
        for wrapped in _wrap(line, 70):
            print(f"    {wrapped}")
    return toolset, registry


async def demo_call(registry: ToolRegistry) -> None:
    rule("3. a call round-tripping, as an ordinary tool call")
    print("  Nothing above the registry knows this one leaves the process.\n")
    show(
        "mcp__docs__search(query='retry policy')",
        await registry.execute(
            ToolUse(id="d1", name="mcp__docs__search", arguments={"query": "retry policy"})
        ),
    )
    show(
        "mcp__tickets__open(query='retry')",
        await registry.execute(
            ToolUse(id="d2", name="mcp__tickets__open", arguments={"query": "retry"})
        ),
    )
    show(
        "mcp__docs__search with a name the server does not have",
        await registry.execute(ToolUse(id="d3", name="mcp__docs__missing", arguments={})),
    )


async def demo_death(
    registry: ToolRegistry, fakes: Mapping[str, FakeServer], clients: Mapping[str, McpClient]
) -> None:
    rule("4. the server dies mid-session — the session does not")
    print("  Killing 'docs' the way a crashed subprocess dies: EOF on the pipe.\n")
    fakes["docs"].kill()

    dead = await registry.execute(
        ToolUse(id="d4", name="mcp__docs__search", arguments={"query": "anything"})
    )
    show("mcp__docs__search after the kill", dead)
    print(
        f"      ↑ {fakes['docs'].connections} connection attempt(s) to 'docs'; "
        f"{clients['docs'].reconnects_used} reconnect used, bounded at 1."
    )

    print("\n  Everything else is untouched:")
    show(
        "mcp__tickets__open (different server)",
        await registry.execute(
            ToolUse(id="d5", name="mcp__tickets__open", arguments={"query": "still here"})
        ),
    )
    show(
        "read notes.md (local tool)",
        await registry.execute(ToolUse(id="d6", name="read", arguments={"path": "notes.md"})),
    )
    show(
        "mcp__docs__search a second time — still a value, not an exception",
        await registry.execute(
            ToolUse(id="d7", name="mcp__docs__search", arguments={"query": "again"})
        ),
    )


async def demo_server(ctx: ToolContext) -> None:
    rule("5. the other direction: Ronin answering MCP")
    print("  A real McpServer over injected streams, driven by a real StdioTransport.")
    print("  No shell was wired into this registry, so 'bash' is a recorded absence")
    print("  rather than a tool that exists and always fails.\n")

    calls: list[str] = []

    async def nested_runner(prompt: str) -> str:
        calls.append(prompt)
        return (
            f"[nested Ronin loop] asked: {prompt}\n"
            "read 2 files, changed none; notes.md documents the retry policy."
        )

    approvals: list[ApprovalRequest] = []

    async def approver(request: ApprovalRequest) -> ApprovalDecision:
        approvals.append(request)
        return ApprovalDecision(approved=True, reason="demo runs unattended in a temp dir")

    local = build_registry(ctx)
    for label, server in (
        ("no approver attached", McpServer(local, nested_runner=nested_runner)),
        (
            "approver attached",
            McpServer(local, nested_runner=nested_runner, approver=approver),
        ),
    ):
        print(f"  ── {label} ──")
        print(f"     tools requested but unavailable: {server.unavailable_tools or 'none'}")
        near, far = memory_duplex()
        task = asyncio.create_task(serve_stdio(server, far))
        transport = StdioTransport(streams=near, timeout=5.0)
        await transport.start()
        try:
            handshake = await transport.send(
                Request(
                    method="initialize",
                    params={
                        "protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "demo", "version": "0"},
                    },
                    id=1,
                )
            )
            assert handshake.result is not None
            print(f"     initialize → {dict(handshake.result)['serverInfo']}")

            listed = await transport.send(Request(method="tools/list", params={}, id=2))
            assert listed.result is not None
            for descriptor in listed.result["tools"]:
                meta = descriptor["_meta"]
                print(
                    f"     {descriptor['name']:<12} "
                    f"danger={meta['ronin/dangerLevel']:<11} "
                    f"gated={meta['ronin/requiresApproval']}"
                )

            called = await transport.send(
                Request(
                    method="tools/call",
                    params={"name": "ronin_task", "arguments": {"prompt": "explain the retries"}},
                    id=3,
                )
            )
            if called.error is not None:
                print(f"     tools/call ronin_task → error {called.error.code}: ")
                for line in _wrap(called.error.message, 64):
                    print(f"       {line}")
            else:
                assert called.result is not None
                print("     tools/call ronin_task → ok")
                for line in str(called.result["content"][0]["text"]).split("\n"):
                    print(f"       {line}")

            bad = await transport.send(Request(method="tools/call", params={"name": 42}, id=4))
            assert bad.error is not None
            print(f"     tools/call with name=42 → {bad.error.code} (invalid params)")
            unknown = await transport.send(Request(method="tools/frobnicate", params={}, id=5))
            assert unknown.error is not None
            print(f"     tools/frobnicate → {unknown.error.code} (method not found)")
            garbage = await transport.send(Request(method="ping", params={}, id=6))
            assert garbage.result is not None
            print("     ping → {} (still healthy after two refusals)")
        finally:
            await transport.close()
            task.cancel()
        print()
    print(f"  nested runner invoked {len(calls)} time(s); approver asked {len(approvals)} time(s).")
    if approvals:
        print(f"  what the human would have been shown: {approvals[0].rendered}")


async def main() -> int:
    print("ronin.mcp — offline demo. In-process servers, real JSON-RPC over real streams.")
    print(f"protocol version: {PROTOCOL_VERSION}")

    configs = parse_mcp_config(CONFIG_JSON)
    assert all(config.transport is TransportKind.STDIO for config in configs)
    fakes = {
        "docs": FakeServer(
            "fake-docs", tools={"search": ("Search the docs corpus.", _docs_search)}
        ),
        "tickets": FakeServer(
            "fake-tickets",
            tools={
                "open": (
                    "Open tickets matching a query.\nReturns ids and titles.",
                    _tickets_open,
                ),
                "search": ("Search all tickets.", _tickets_open),
            },
        ),
        "memo": FakeServer("fake-memo", tools={}, capabilities=("prompts",)),
    }
    with tempfile.TemporaryDirectory(prefix="ronin-mcp-demo-") as tmp:
        root = Path(tmp)
        (root / "notes.md").write_text("# notes\nthe registry is real\n", encoding="utf-8")
        ctx = ToolContext(root=root, env={"PATH": "/usr/bin:/bin"})
        toolset, registry = await demo_handshake(configs, fakes, ctx)
        try:
            await demo_call(registry)
            await demo_death(registry, fakes, toolset.clients)
            await demo_server(ctx)
        finally:
            await toolset.aclose()
            for fake in fakes.values():
                fake.kill()

    rule("done")
    print("The two rules worth remembering: a remote tool of unknown danger is gated,")
    print("and a dead server's tools return a sentence the model can act on rather")
    print("than an exception that ends the turn.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
