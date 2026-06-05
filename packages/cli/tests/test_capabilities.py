"""Tests for the agent capability-awareness block."""
from __future__ import annotations

from ronin_cli.capabilities import capability_block, connected_mcp_servers


def test_connected_mcp_servers_from_tool_names() -> None:
    names = ["read_file", "github__create_issue", "github__list_prs", "slack__post"]
    assert connected_mcp_servers(names) == ["github", "slack"]   # deduped, sorted
    assert connected_mcp_servers(["read_file", "weather"]) == []  # no MCP tools


def test_capability_block_states_ronin_commands() -> None:
    block = capability_block(["read_file"])
    assert "ronin mcp install" in block
    assert "ronin plugin add" in block
    assert "ronin plugin new" in block
    # the key behavioural instruction: don't give a generic tutorial
    assert "do NOT give a generic" in block


def test_capability_block_lists_connected_servers() -> None:
    block = capability_block(["github__x", "github__y", "read_file"])
    assert "Currently connected MCP servers" in block and "github" in block


def test_capability_block_omits_servers_line_when_none() -> None:
    block = capability_block(["read_file", "write_file"])
    assert "Currently connected MCP servers" not in block
