from __future__ import annotations

import sqlite3
from collections.abc import Callable
from types import SimpleNamespace

import pytest

from ronin_mcp_servers import (
    DangerousSQLError,
    PostgresQueryTool,
    is_readonly_sql,
    run_query,
)
from ronin_mcp_servers.postgres import SERVER_CLASSES, load_server_class


@pytest.mark.parametrize("sql", [
    "SELECT 1",
    "select id, name from users",
    "  SELECT * FROM users WHERE active",
    "WITH recent AS (SELECT * FROM events) SELECT count(*) FROM recent",
])
def test_safe_sql_allowed(sql: str) -> None:
    allowed, reason = is_readonly_sql(sql)
    assert allowed, f"should allow but rejected: {reason}"


@pytest.mark.parametrize("sql,expected_reason_substring", [
    ("INSERT INTO users VALUES (1, 'evil')", "destructive"),
    ("UPDATE users SET name='evil'", "destructive"),
    ("DELETE FROM users", "destructive"),
    ("DROP TABLE users", "destructive"),
    ("TRUNCATE users", "destructive"),
    ("ALTER TABLE users ADD COLUMN ssn TEXT", "destructive"),
    ("CREATE TABLE x (id int)", "destructive"),
    ("GRANT ALL ON users TO evil", "destructive"),
    ("SELECT 1; DROP TABLE users", "single-statement"),
    ("SELECT * INTO temp FROM users", "INTO"),
    ("EXPLAIN ANALYZE SELECT * FROM users", "SELECT or WITH"),
    ("", "empty"),
])
def test_dangerous_sql_rejected(sql: str, expected_reason_substring: str) -> None:
    allowed, reason = is_readonly_sql(sql)
    assert not allowed
    assert expected_reason_substring.lower() in reason.lower()


def test_run_query_executes_against_sqlite() -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        "CREATE TABLE users (id INTEGER, name TEXT);"
        "INSERT INTO users VALUES (1, 'alice');"
        "INSERT INTO users VALUES (2, 'bob');"
    )
    rows = run_query(conn, "SELECT id, name FROM users ORDER BY id")
    assert rows == [{"id": 1, "name": "alice"}, {"id": 2, "name": "bob"}]


def test_run_query_truncates_to_max_rows() -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript("CREATE TABLE n (v INTEGER)")
    for i in range(50):
        conn.execute("INSERT INTO n VALUES (?)", (i,))
    rows = run_query(conn, "SELECT v FROM n ORDER BY v", max_rows=5)
    assert len(rows) == 5
    assert rows[0] == {"v": 0}


def test_run_query_rejects_dangerous_sql() -> None:
    conn = sqlite3.connect(":memory:")
    with pytest.raises(DangerousSQLError):
        run_query(conn, "DROP TABLE x")


def test_postgres_query_tool_proxies_to_run_query() -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript("CREATE TABLE t (x INT); INSERT INTO t VALUES (42)")
    tool = PostgresQueryTool(connection=conn)
    assert tool.query("SELECT x FROM t") == [{"x": 42}]


# --- load_server_class: which mcp SDK, and what it says when there isn't one ---
#
# These exist because the original import sat inside `serve()`, a method marked
# `# pragma: no cover`, so the one line that had to change across an mcp major
# was also the one line no test could reach. Injecting the importer makes the
# version handling ordinary testable code.


class _MCPServerStub:
    """Stands in for mcp >= 2's ``mcp.server.mcpserver.MCPServer``."""


class _FastMCPStub:
    """Stands in for mcp < 2's ``mcp.server.fastmcp.FastMCP``."""


def _importer(modules: dict[str, object]) -> Callable[[str], object]:
    """An ``import_module`` that only knows about ``modules``."""

    def _import(name: str) -> object:
        if name not in modules:
            raise ModuleNotFoundError(f"No module named {name!r}")
        return modules[name]

    return _import


def _sdk(*, mcpserver: bool, fastmcp: bool) -> dict[str, object]:
    """A fake installed ``mcp`` exposing whichever entry points are asked for."""
    modules: dict[str, object] = {"mcp": SimpleNamespace()}
    if mcpserver:
        modules["mcp.server.mcpserver"] = SimpleNamespace(MCPServer=_MCPServerStub)
    if fastmcp:
        modules["mcp.server.fastmcp"] = SimpleNamespace(FastMCP=_FastMCPStub)
    return modules


def test_mcp_2x_is_found_rather_than_reported_missing() -> None:
    """The regression this was written for.

    On mcp 2.x ``mcp.server.fastmcp`` is gone. The previous code caught the
    resulting ModuleNotFoundError — an ImportError subclass — and raised "mcp SDK
    not installed", sending the reader to install a package already present.
    """
    found = load_server_class(_importer(_sdk(mcpserver=True, fastmcp=False)))
    assert found is _MCPServerStub


def test_mcp_1x_still_works() -> None:
    found = load_server_class(_importer(_sdk(mcpserver=False, fastmcp=True)))
    assert found is _FastMCPStub


def test_newest_spelling_wins_when_both_are_present() -> None:
    found = load_server_class(_importer(_sdk(mcpserver=True, fastmcp=True)))
    assert found is _MCPServerStub


def test_a_module_without_the_attribute_falls_through() -> None:
    """Present-but-empty is not the same as absent, and must not end the search."""
    modules = _sdk(mcpserver=False, fastmcp=True)
    modules["mcp.server.mcpserver"] = SimpleNamespace()  # no MCPServer on it
    assert load_server_class(_importer(modules)) is _FastMCPStub


def test_absent_sdk_says_it_is_not_installed() -> None:
    with pytest.raises(RuntimeError) as caught:
        load_server_class(_importer({}))
    assert "not installed" in str(caught.value)


def test_unsupported_version_is_not_described_as_missing() -> None:
    """The distinction the old message collapsed: installed, but unusable."""
    with pytest.raises(RuntimeError) as caught:
        load_server_class(_importer(_sdk(mcpserver=False, fastmcp=False)))
    message = str(caught.value)
    assert "not installed" not in message
    for module_name, attribute in SERVER_CLASSES:
        assert f"{module_name}.{attribute}" in message
