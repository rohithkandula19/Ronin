"""Tests for the MCP server catalog."""
from __future__ import annotations

from ronin_cli.mcp_catalog import CATALOG, KnownServer, catalog_rows, resolve


def test_resolve_by_key() -> None:
    gh = resolve("github")
    assert gh and gh.command == "npx"
    assert "GITHUB_PERSONAL_ACCESS_TOKEN" in gh.env


def test_resolve_by_alias_case_insensitive() -> None:
    assert resolve("mail").key == "gmail"        # alias
    assert resolve("  GitHub ").key == "github"  # whitespace + case
    assert resolve("fs").key == "filesystem"


def test_resolve_unknown_is_none() -> None:
    assert resolve("definitely-not-a-server") is None
    assert resolve("") is None


def test_catalog_rows_cover_every_server() -> None:
    rows = catalog_rows()
    assert len(rows) == len(CATALOG)
    keys = {k for k, _, _ in rows}
    assert {"github", "gmail", "slack", "postgres"} <= keys
    # a server with no env shows the em-dash placeholder
    fs = next(r for r in rows if r[0] == "filesystem")
    assert fs[1] == "—"


def test_known_server_shapes_are_valid() -> None:
    for s in CATALOG.values():
        assert isinstance(s, KnownServer)
        assert s.command and s.args and s.blurb
