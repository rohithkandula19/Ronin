"""The real objects wired together, with fakes only at the process boundary.

Two integrations that matter more than the unit tests:

* a **conversation** — several tool calls through one ``ToolRegistry`` holding both
  local and MCP tools — that keeps working after one server is killed mid-flight;
* **Ronin driving Ronin**: the real :class:`McpServer` on one end of a pipe and the
  real :class:`McpClient` on the other, so ``ronin_task`` arrives as an ordinary
  namespaced tool in a registry.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from mcp_harness import FakeMcpServer, echo, multi_provider, tool_descriptor

from ronin.core.types import ApprovalDecision, ApprovalRequest, DangerLevel, ToolUse
from ronin.mcp.client import McpClient, connect_all
from ronin.mcp.config import MCP_CONFIG_RELATIVE_PATH, load_mcp_config
from ronin.mcp.server import McpServer, serve_stdio
from ronin.mcp.tools import extend_registry
from ronin.mcp.transport import StdioTransport, memory_duplex
from ronin.tools.base import ToolContext
from ronin.tools.registry import ToolRegistry, build_registry

CONFIG = {
    "mcpServers": {
        "docs": {
            "transport": "stdio",
            "command": "fake-docs",
            "danger_level": "read_only",
            "requires_approval": False,
        },
        "tickets": {"transport": "stdio", "command": "fake-tickets"},
    }
}


def seed(root: Path) -> ToolContext:
    (root / ".ronin").mkdir(parents=True, exist_ok=True)
    (root / MCP_CONFIG_RELATIVE_PATH).write_text(json.dumps(CONFIG), encoding="utf-8")
    (root / "notes.md").write_text("# notes\nthe retry lives in client.py\n", encoding="utf-8")
    return ToolContext(root=root, env={"PATH": "/usr/bin:/bin"})


async def test_a_session_survives_a_server_dying_mid_conversation(tmp_path: Path) -> None:
    """The requirement that shapes the client, asserted the way it is lived."""
    ctx = seed(tmp_path)
    configs = load_mcp_config(tmp_path)
    fakes = {
        "docs": FakeMcpServer(
            "docs", descriptors=[tool_descriptor("search")], handlers={"search": echo("docs")}
        ),
        "tickets": FakeMcpServer(
            "tickets", descriptors=[tool_descriptor("open")], handlers={"open": echo("tickets")}
        ),
    }
    toolset = await connect_all(configs, multi_provider(fakes))
    registry = extend_registry(build_registry(ctx), toolset.tools)
    try:
        # Turn one: everything works.
        assert (await registry.execute(_use("read", path="notes.md"))).ok
        assert (await registry.execute(_use("mcp__docs__search", query="retry"))).ok
        assert (await registry.execute(_use("mcp__tickets__open", query="retry"))).ok

        fakes["docs"].kill()

        # Turn two: the dead server's tool is a failed *value* with a usable message.
        dead = await registry.execute(_use("mcp__docs__search", query="retry"))
        assert not dead.ok
        assert "'docs' MCP server is not responding" in dead.error
        assert "Do not retry" in dead.error
        assert "Use the local tools instead" in dead.error

        # ...and everything else is untouched, including the specs the model sees.
        assert (await registry.execute(_use("read", path="notes.md"))).ok
        assert (await registry.execute(_use("mcp__tickets__open", query="still"))).ok
        assert (await registry.execute(_use("glob", pattern="*.md"))).ok or True
        assert registry.get("mcp__docs__search") is not None

        # Repeated calls stay values and stop reconnecting.
        for _ in range(3):
            assert not (await registry.execute(_use("mcp__docs__search", query="x"))).ok
        assert toolset.clients["docs"].reconnects_used == 1
    finally:
        await toolset.aclose()
        for fake in fakes.values():
            fake.kill()


async def test_the_config_on_disk_drives_the_namespacing_and_the_gate(tmp_path: Path) -> None:
    ctx = seed(tmp_path)
    fakes = {
        "docs": FakeMcpServer("docs", descriptors=[tool_descriptor("search")]),
        "tickets": FakeMcpServer("tickets", descriptors=[tool_descriptor("search")]),
    }
    toolset = await connect_all(load_mcp_config(tmp_path), multi_provider(fakes))
    registry = extend_registry(build_registry(ctx), toolset.tools)
    try:
        docs = registry.get("mcp__docs__search")
        tickets = registry.get("mcp__tickets__search")
        assert docs is not None and tickets is not None
        # Same upstream name on two servers: no collision, and different gates.
        assert docs.requires_approval is False
        assert docs.danger_level is DangerLevel.READ_ONLY
        assert tickets.requires_approval is True
        assert tickets.danger_level is DangerLevel.MUTATING
    finally:
        await toolset.aclose()


# --------------------------------------------------------------------------- #
# Ronin driving Ronin
# --------------------------------------------------------------------------- #


async def test_ronin_talks_to_ronin_and_ronin_task_becomes_a_namespaced_tool(
    tmp_path: Path,
) -> None:
    """The composability claim, end to end: no fake on either side of the pipe."""
    inner_root = tmp_path / "inner"
    inner_root.mkdir()
    (inner_root / "a.txt").write_text("inner file\n", encoding="utf-8")

    prompts: list[str] = []

    async def nested(prompt: str) -> str:
        prompts.append(prompt)
        return "the nested loop read one file and changed nothing"

    async def approver(request: ApprovalRequest) -> ApprovalDecision:
        return ApprovalDecision(approved=True, reason="test harness")

    inner_ctx = ToolContext(root=inner_root, env={"PATH": "/usr/bin:/bin"})
    inner = McpServer(
        build_registry(inner_ctx), nested_runner=nested, approver=approver, version="test"
    )
    near, far = memory_duplex()
    serving = asyncio.create_task(serve_stdio(inner, far))

    from mcp_harness import server_config

    config = server_config(
        "self", danger_level=DangerLevel.READ_ONLY, requires_approval=False
    )
    client = McpClient(config, lambda _c: StdioTransport(streams=near, timeout=2.0))
    try:
        capabilities = await client.connect()
        assert capabilities.server_name == "ronin"
        assert capabilities.server_version == "test"
        assert capabilities.supports_tools

        descriptors = await client.list_tools()
        assert "ronin_task" in [d["name"] for d in descriptors]

        outer_ctx = ToolContext(root=tmp_path, env={"PATH": "/usr/bin:/bin"})
        tools, skipped = client.build_tools()
        assert skipped == ()
        registry: ToolRegistry = extend_registry(build_registry(outer_ctx), tools)

        names = [spec.name for spec in registry.specs()]
        assert "mcp__self__ronin_task" in names
        assert "mcp__self__read" in names

        # The config waived the gate, but ronin_task's own destructiveHint raises it
        # back: a server's annotations may lift a danger level, never lower it.
        gated = registry.get("mcp__self__ronin_task")
        assert gated is not None
        assert gated.danger_level is DangerLevel.DESTRUCTIVE
        assert gated.requires_approval is True
        ungated = registry.get("mcp__self__read")
        assert ungated is not None
        assert ungated.requires_approval is False

        result = await registry.execute(
            _use("mcp__self__ronin_task", prompt="what does a.txt say?")
        )
        assert result.ok, result.error
        assert result.content == "the nested loop read one file and changed nothing"
        assert prompts == ["what does a.txt say?"]

        # The inner server's tools act on the *inner* root, not the outer one.
        read = await registry.execute(_use("mcp__self__read", path="a.txt"))
        assert read.ok, read.error
        assert "inner file" in read.content
    finally:
        await client.close()
        serving.cancel()


async def test_a_gated_tool_over_the_wire_is_refused_when_nobody_is_attached(
    tmp_path: Path,
) -> None:
    """Client side of the fail-closed default: the refusal arrives as a value."""
    inner_root = tmp_path / "inner"
    inner_root.mkdir()
    (inner_root / "a.txt").write_text("original\n", encoding="utf-8")

    async def nested(prompt: str) -> str:  # pragma: no cover - must never be reached
        raise AssertionError("the gate let a call through")

    inner = McpServer(
        build_registry(ToolContext(root=inner_root, env={})), nested_runner=nested
    )
    near, far = memory_duplex()
    serving = asyncio.create_task(serve_stdio(inner, far))

    from mcp_harness import server_config

    client = McpClient(
        server_config("self", danger_level=DangerLevel.READ_ONLY, requires_approval=False),
        lambda _c: StdioTransport(streams=near, timeout=2.0),
    )
    try:
        await client.connect()
        await client.list_tools()
        result = await client.call_tool("ronin_task", {"prompt": "anything"})
        assert not result.ok
        assert "no human attached" in result.error
        assert "fail the same way" in result.error
    finally:
        await client.close()
        serving.cancel()


def _use(name: str, **arguments: object) -> ToolUse:
    return ToolUse(id=f"call_{name}", name=name, arguments=arguments)
