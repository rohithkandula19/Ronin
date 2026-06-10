from __future__ import annotations

import sqlite3

import pytest

from ronin_mcp_servers import (
    DangerousSQLError,
    PostgresQueryTool,
    is_readonly_sql,
    run_query,
)


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
