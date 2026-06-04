"""Tests for MCP server env support (the GitHub-token path)."""
from __future__ import annotations

import json
from pathlib import Path

from ronin_cli.mcp_client import add_mcp_server, load_mcp_servers, parse_env_pairs


def test_parse_env_pairs_keyvalue() -> None:
    assert parse_env_pairs(["A=1", "B=two=2"]) == {"A": "1", "B": "two=2"}


def test_parse_env_bare_key_inherits_from_shell(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_PERSONAL_ACCESS_TOKEN", "ghp_secret")
    assert parse_env_pairs(["GITHUB_PERSONAL_ACCESS_TOKEN"]) == {
        "GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_secret"}


def test_add_mcp_server_writes_env(tmp_path: Path) -> None:
    add_mcp_server("github", "npx", ["-y", "@modelcontextprotocol/server-github"],
                   tmp_path, env={"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_x"})
    spec = load_mcp_servers(tmp_path)["github"]
    assert spec["command"] == "npx"
    assert spec["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"] == "ghp_x"


def test_add_mcp_server_no_env_omits_key(tmp_path: Path) -> None:
    add_mcp_server("fs", "npx", ["-y", "server-filesystem", "."], tmp_path)
    assert "env" not in load_mcp_servers(tmp_path)["fs"]
