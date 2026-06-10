"""Read-only Postgres MCP server reference.

Two layers:

1. ``is_readonly_sql`` + ``run_query`` — pure-Python safety + execution. Pluggable
   connection (anything with a DB-API ``cursor()`` method). Tested against sqlite.
2. ``PostgresQueryTool.serve()`` — wires the above into an MCP server via the
   optional ``mcp`` SDK. Run with ``python -m ronin_mcp_servers.postgres``.

The safety check rejects multi-statement queries, anything starting with a non-SELECT/WITH
keyword, and queries containing destructive keywords. It is a defense-in-depth layer —
back it up with a Postgres role that has SELECT-only privileges in production.
"""
from __future__ import annotations

import os
import re
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field


_FIRST_KEYWORD = re.compile(r"(?i)^\s*(select|with)\b")
_DESTRUCTIVE_KEYWORDS = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|"
    r"copy|merge|replace|vacuum|reindex|cluster|comment|attach|detach)\b",
    re.IGNORECASE,
)
_SELECT_INTO = re.compile(r"(?i)\bselect\b[^;]*\binto\b")


class DangerousSQLError(Exception):
    """Raised when a query fails the read-only safety check."""


def is_readonly_sql(sql: str) -> tuple[bool, str]:
    """Return ``(allowed, reason)``. ``reason`` is empty when allowed.

    Allows: single-statement ``SELECT`` or ``WITH ... SELECT`` queries.
    Rejects: multi-statement, write keywords, ``SELECT ... INTO``.
    """
    statements = [s.strip() for s in sql.split(";") if s.strip()]
    if len(statements) == 0:
        return False, "empty query"
    if len(statements) > 1:
        return False, "only single-statement queries allowed"

    stmt = statements[0]
    if _SELECT_INTO.search(stmt):
        return False, "SELECT INTO is not allowed (creates a table)"
    if _DESTRUCTIVE_KEYWORDS.search(stmt):
        return False, "destructive keyword detected"
    if not _FIRST_KEYWORD.match(stmt):
        return False, "only SELECT or WITH queries allowed"

    return True, ""


class _DBAPIConnection(Protocol):
    """Subset of PEP 249 we need: anything with cursor() works."""

    def cursor(self) -> Any: ...


def run_query(connection: _DBAPIConnection, sql: str, max_rows: int = 1000) -> list[dict[str, Any]]:
    """Run a read-only SQL query and return rows as dicts.

    Raises ``DangerousSQLError`` if the safety check fails.
    Truncates to ``max_rows`` defensively to avoid blowing up the agent's context.
    """
    allowed, reason = is_readonly_sql(sql)
    if not allowed:
        raise DangerousSQLError(reason)

    cur = connection.cursor()
    try:
        cur.execute(sql)
        rows = cur.fetchmany(max_rows)
        columns = [desc[0] for desc in (cur.description or [])]
        return [dict(zip(columns, row)) for row in rows]
    finally:
        cur.close()


class PostgresQueryTool(BaseModel):
    """MCP-server-ready wrapper around ``run_query``.

    For direct use, call ``query()`` from your agent's tool handler. For MCP transport,
    call ``serve()`` to expose a stdio MCP server (requires the ``mcp`` extra installed).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    connection: Any
    max_rows: int = 1000
    name: str = "postgres-query"
    description: str = (
        "Execute a read-only SQL query against the configured database. "
        "Only SELECT / WITH queries are permitted; destructive statements are rejected."
    )

    def query(self, sql: str) -> list[dict[str, Any]]:
        return run_query(self.connection, sql, max_rows=self.max_rows)

    def serve(self) -> None:  # pragma: no cover — requires mcp extra
        """Run an MCP server over stdio. Requires ``pip install ronin-mcp-servers[mcp]``."""
        try:
            from mcp.server.fastmcp import FastMCP
        except ImportError as exc:
            raise RuntimeError(
                "mcp SDK not installed. Run `pip install ronin-mcp-servers[mcp]`."
            ) from exc

        server = FastMCP(self.name)

        @server.tool(description=self.description)
        def query(sql: str) -> list[dict[str, Any]]:  # noqa: ARG001
            return self.query(sql)

        server.run()


def _connect_from_env() -> Any:  # pragma: no cover — needs psycopg2 + a DB
    import psycopg2  # type: ignore[import-not-found]

    return psycopg2.connect(os.environ["DATABASE_URL"])


def main() -> None:  # pragma: no cover
    """Entry point: connect using ``DATABASE_URL`` from env and serve over stdio MCP."""
    connection = _connect_from_env()
    PostgresQueryTool(connection=connection).serve()


if __name__ == "__main__":  # pragma: no cover
    main()
