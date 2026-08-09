"""The client: handshake, capability negotiation, and surviving a dead server.

The degradation tests are the point of this file. A killed server must leave the
session usable, its tools must fail as *values* with a message the model can act
on, and the reconnect budget must be spent at most once.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pytest
from mcp_harness import (
    FakeMcpServer,
    ScriptedSender,
    echo,
    multi_provider,
    server_config,
    sse_bytes,
    text_result,
    tool_descriptor,
)

from ronin.core.types import DangerLevel
from ronin.mcp.client import (
    MAX_TOOL_LIST_PAGES,
    PROTOCOL_VERSION,
    McpClient,
    ServerCapabilities,
    connect_all,
    default_transport_provider,
    render_content,
)
from ronin.mcp.config import ConfigError, McpServerConfig, TransportKind
from ronin.mcp.jsonrpc import INVALID_PARAMS, ProtocolError
from ronin.mcp.transport import SseTransport, StdioTransport


def client_for(fake: FakeMcpServer, **kwargs: Any) -> McpClient:
    return McpClient(
        server_config(
            fake.name,
            danger_level=DangerLevel.READ_ONLY,
            requires_approval=False,
        ),
        fake.provider(),
        **kwargs,
    )


# --------------------------------------------------------------------------- #
# Handshake and capabilities
# --------------------------------------------------------------------------- #


async def test_the_handshake_records_what_the_server_said() -> None:
    fake = FakeMcpServer("docs", descriptors=[tool_descriptor("search")])
    client = client_for(fake)
    capabilities = await client.connect()
    assert capabilities.protocol_version == PROTOCOL_VERSION
    assert capabilities.server_name == "docs"
    assert capabilities.supports_tools
    assert not capabilities.supports_resources
    assert client.alive
    await client.close()


async def test_the_initialized_notification_is_sent_and_not_answered() -> None:
    fake = FakeMcpServer("docs", descriptors=[tool_descriptor("search")])
    client = client_for(fake)
    await client.connect()
    # tools/list forces the ordering: the pipe is in-order, so a reply to it
    # proves the notification ahead of it was consumed.
    await client.list_tools()
    methods = [request.method for request in fake.requests]
    assert methods == ["initialize", "notifications/initialized", "tools/list"]
    assert fake.requests[1].is_notification
    await client.close()


async def test_an_unsupported_protocol_version_refuses_the_connection() -> None:
    """Guessing at an unknown revision's payload shape is how fields get dropped."""
    fake = FakeMcpServer("docs", protocol_version="1999-01-01")
    client = client_for(fake)
    with pytest.raises(ProtocolError, match="1999-01-01"):
        await client.connect()
    assert not client.alive
    await client.close()


async def test_a_server_with_no_tools_capability_lists_nothing_without_asking() -> None:
    fake = FakeMcpServer("memo", descriptors=[tool_descriptor("x")], capabilities=("prompts",))
    client = client_for(fake)
    capabilities = await client.connect()
    assert not capabilities.supports_tools
    assert await client.list_tools() == ()
    assert "tools/list" not in [request.method for request in fake.requests]
    await client.close()


async def test_listing_before_connecting_is_a_programming_error() -> None:
    fake = FakeMcpServer("docs")
    client = client_for(fake)
    with pytest.raises(Exception, match="before connect"):
        await client.list_tools()


def test_capabilities_survive_a_server_that_omits_everything() -> None:
    capabilities = ServerCapabilities.from_initialize({})
    assert capabilities.protocol_version == ""
    assert not capabilities.supports_tools
    assert capabilities.raw == {}


# --------------------------------------------------------------------------- #
# Listing
# --------------------------------------------------------------------------- #


async def test_tools_list_follows_the_cursor() -> None:
    fake = FakeMcpServer(
        "docs",
        descriptors=[tool_descriptor("a")],
        extra_pages=[[tool_descriptor("b")], [tool_descriptor("c")]],
    )
    client = client_for(fake)
    await client.connect()
    descriptors = await client.list_tools()
    assert [d["name"] for d in descriptors] == ["a", "b", "c"]
    await client.close()


class EndlessPages(FakeMcpServer):
    """A server whose cursor always points at another page."""

    def _list(self, cursor: object) -> Mapping[str, Any]:
        return {"tools": [tool_descriptor("a")], "nextCursor": "1"}


class WrongShape(FakeMcpServer):
    """A server that answers tools/list with something that is not a list."""

    def _list(self, cursor: object) -> Mapping[str, Any]:
        return {"tools": "not-a-list"}


async def test_endless_pagination_is_refused_rather_than_looped() -> None:
    client = client_for(EndlessPages("docs"))
    await client.connect()
    with pytest.raises(ProtocolError, match=str(MAX_TOOL_LIST_PAGES)):
        await client.list_tools()
    await client.close()


async def test_a_tools_key_of_the_wrong_shape_is_a_protocol_error() -> None:
    client = client_for(WrongShape("docs"))
    await client.connect()
    with pytest.raises(ProtocolError, match="expected a list"):
        await client.list_tools()
    await client.close()


# --------------------------------------------------------------------------- #
# Calling
# --------------------------------------------------------------------------- #


async def test_a_successful_call_returns_the_text_content() -> None:
    fake = FakeMcpServer(
        "docs", descriptors=[tool_descriptor("search")], handlers={"search": echo("found")}
    )
    client = client_for(fake)
    await client.connect()
    await client.list_tools()
    result = await client.call_tool("search", {"query": "x"})
    assert result.ok
    assert result.content == "found:[('query', 'x')]"
    await client.close()


async def test_an_is_error_result_becomes_a_failed_tool_result() -> None:
    def failing(arguments: Any) -> Any:
        return {"content": [{"type": "text", "text": "the index is cold"}], "isError": True}

    fake = FakeMcpServer("docs", handlers={"search": failing})
    client = client_for(fake)
    await client.connect()
    result = await client.call_tool("search", {})
    assert not result.ok
    assert result.error == "the index is cold"
    await client.close()


async def test_a_jsonrpc_error_becomes_a_failed_tool_result_that_says_not_to_retry() -> None:
    fake = FakeMcpServer("docs", handlers={})
    client = client_for(fake)
    await client.connect()
    result = await client.call_tool("nope", {})
    assert not result.ok
    assert str(INVALID_PARAMS) in result.error
    assert "fail the same way" in result.error
    await client.close()


async def test_structured_content_is_used_when_there_is_no_text() -> None:
    fake = FakeMcpServer(
        "docs", handlers={"q": lambda _a: {"structuredContent": {"rows": 3}, "content": []}}
    )
    client = client_for(fake)
    await client.connect()
    result = await client.call_tool("q", {})
    assert result.ok
    assert json.loads(result.content) == {"rows": 3}
    await client.close()


# --------------------------------------------------------------------------- #
# Degradation
# --------------------------------------------------------------------------- #


async def test_a_server_killed_mid_session_fails_as_a_value_not_an_exception() -> None:
    fake = FakeMcpServer(
        "docs", descriptors=[tool_descriptor("search")], handlers={"search": echo()}
    )
    client = client_for(fake)
    await client.connect()
    await client.list_tools()
    assert (await client.call_tool("search", {})).ok

    fake.kill()
    result = await client.call_tool("search", {})
    assert not result.ok
    assert "'docs' MCP server is not responding" in result.error
    assert "Do not retry them" in result.error
    assert "mcp__docs__*" in result.error
    await client.close()


async def test_the_reconnect_budget_is_spent_once_and_then_respected() -> None:
    """An unbounded reconnect against a server that crashes on start is a hot loop."""
    fake = FakeMcpServer("docs", handlers={"search": echo()})
    client = client_for(fake, max_reconnects=1)
    await client.connect()
    fake.kill()
    connections_before = fake.connections
    for _ in range(4):
        assert not (await client.call_tool("search", {})).ok
    assert client.reconnects_used == 1
    assert fake.connections == connections_before + 1
    await client.close()


async def test_a_reconnect_that_works_recovers_the_tool() -> None:
    """`die_after` makes the server answer N requests and then drop the pipe."""
    fake = FakeMcpServer(
        "docs",
        descriptors=[tool_descriptor("search")],
        handlers={"search": echo("after")},
        die_after=2,
    )
    client = client_for(fake, max_reconnects=1)
    await client.connect()
    await client.list_tools()
    result = await client.call_tool("search", {})
    assert result.ok, result.error
    assert result.content.startswith("after")
    assert client.reconnects_used == 1
    assert fake.connections == 2
    await client.close()


async def test_a_zero_reconnect_budget_never_reopens_the_connection() -> None:
    fake = FakeMcpServer("docs", handlers={"search": echo()})
    client = client_for(fake, max_reconnects=0)
    await client.connect()
    fake.kill()
    assert not (await client.call_tool("search", {})).ok
    assert client.reconnects_used == 0
    assert fake.connections == 1
    await client.close()


# --------------------------------------------------------------------------- #
# connect_all
# --------------------------------------------------------------------------- #


async def test_connect_all_records_a_failure_instead_of_raising() -> None:
    fakes = {
        "good": FakeMcpServer(
            "good", descriptors=[tool_descriptor("search")], handlers={"search": echo()}
        ),
        "old": FakeMcpServer("old", protocol_version="1999-01-01"),
        "memo": FakeMcpServer("memo", capabilities=("prompts",)),
        "empty": FakeMcpServer("empty", descriptors=[]),
    }
    configs = [
        server_config(name, danger_level=DangerLevel.READ_ONLY, requires_approval=False)
        for name in fakes
    ]
    toolset = await connect_all(configs, multi_provider(fakes))
    assert [tool.name for tool in toolset.tools] == ["mcp__good__search"]
    assert set(toolset.failures) == {"old", "memo", "empty"}
    assert "1999-01-01" in toolset.failures["old"]
    assert "no 'tools' capability" in toolset.failures["memo"]
    assert "listed none" in toolset.failures["empty"]
    assert "good: 1 tool(s)" in toolset.summary()
    await toolset.aclose()


async def test_connect_all_skips_disabled_servers() -> None:
    fakes = {"a": FakeMcpServer("a", descriptors=[tool_descriptor("t")])}
    disabled = McpServerConfig(
        name="a", transport=TransportKind.STDIO, command="fake", enabled=False
    )
    toolset = await connect_all([disabled], multi_provider(fakes))
    assert toolset.tools == ()
    assert toolset.clients == {}
    assert fakes["a"].connections == 0


async def test_a_skipped_tool_name_is_reported_on_the_toolset() -> None:
    fakes = {"docs": FakeMcpServer("docs", descriptors=[tool_descriptor("not.legal")])}
    toolset = await connect_all([server_config("docs")], multi_provider(fakes))
    assert toolset.tools == ()
    assert len(toolset.skipped) == 1
    assert "not.legal" in toolset.skipped[0]
    assert "skipped" in toolset.summary()
    await toolset.aclose()


async def test_an_empty_toolset_says_so() -> None:
    toolset = await connect_all([], multi_provider({}))
    assert toolset.summary() == "no MCP servers configured"


# --------------------------------------------------------------------------- #
# Content rendering
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("blocks", "needle"),
    [
        ([{"type": "text", "text": "hello"}], "hello"),
        ([{"type": "image", "mimeType": "image/png", "data": "AAAA"}], "image block omitted"),
        ([{"type": "audio", "mimeType": "audio/wav", "data": "AA"}], "audio block omitted"),
        (
            [{"type": "resource", "resource": {"uri": "file:///x", "text": "body"}}],
            "file:///x",
        ),
        ([{"type": "resource", "resource": {"uri": "file:///x"}}], "carried no text"),
        ([{"type": "video", "data": "x"}], "unsupported MCP content block"),
        (["bare string"], "bare string"),
        ([7], "unrenderable MCP content entry"),
    ],
)
def test_every_content_block_renders_or_is_marked(blocks: Any, needle: str) -> None:
    """A silently dropped block is a model wondering why the tool said nothing."""
    assert needle in render_content(blocks)


def test_a_missing_content_key_renders_as_empty() -> None:
    assert render_content(None) == ""


# --------------------------------------------------------------------------- #
# The default provider
# --------------------------------------------------------------------------- #


def test_the_default_provider_builds_a_stdio_transport_without_spawning() -> None:
    transport = default_transport_provider()(server_config("docs"))
    assert isinstance(transport, StdioTransport)


def test_a_network_server_without_a_sender_is_a_config_error() -> None:
    """'I configured a remote server and no tools appeared' is the failure to avoid."""
    provider = default_transport_provider()
    with pytest.raises(ConfigError, match="needs an HTTP sender"):
        provider(server_config("r", transport=TransportKind.SSE))


async def test_the_default_provider_wires_an_sse_transport_to_the_sender() -> None:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}})
    sender = ScriptedSender([sse_bytes(body)], chunk_size=1)
    provider = default_transport_provider(sender=sender)
    transport = provider(server_config("r", transport=TransportKind.SSE))
    assert isinstance(transport, SseTransport)


async def test_a_client_over_a_network_transport_handshakes_the_same_way() -> None:
    """The client is transport-agnostic; this is the assertion that proves it."""
    initialize = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "remote", "version": "1"},
            },
        }
    )
    listed = json.dumps(
        {"jsonrpc": "2.0", "id": 2, "result": {"tools": [tool_descriptor("search")]}}
    )
    called = json.dumps({"jsonrpc": "2.0", "id": 3, "result": text_result("remote answer")})
    sender = ScriptedSender(
        [sse_bytes(initialize), b"", sse_bytes(listed), sse_bytes(called)], chunk_size=1
    )
    config = server_config(
        "remote",
        transport=TransportKind.SSE,
        danger_level=DangerLevel.READ_ONLY,
        requires_approval=False,
    )
    client = McpClient(config, default_transport_provider(sender=sender))
    await client.connect()
    descriptors = await client.list_tools()
    assert [d["name"] for d in descriptors] == ["search"]
    result = await client.call_tool("search", {"query": "x"})
    assert result.ok
    assert result.content == "remote answer"
    await client.close()


async def test_calling_a_tool_on_a_client_that_never_connected_is_still_a_value() -> None:
    """`call_tool` is a tool boundary: it must not raise even when misused."""
    client = client_for(FakeMcpServer("docs"), max_reconnects=0)
    result = await client.call_tool("search", {})
    assert not result.ok
    assert "not responding" in result.error
