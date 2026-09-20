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

import importlib
import os
import re
from collections.abc import Callable
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
        server = load_server_class()(self.name)

        @server.tool(description=self.description)
        def query(sql: str) -> list[dict[str, Any]]:  # noqa: ARG001
            return self.query(sql)

        server.run()


# The server class the `mcp` SDK exposes, newest spelling first. mcp 2.0 renamed
# FastMCP to MCPServer and moved it; for this module the two are shape-compatible
# — both take the server name positionally, both carry `.tool(description=...)`
# and a `.run()` that defaults to stdio — so supporting the pair costs a lookup
# table rather than a branch at every call site.
SERVER_CLASSES: tuple[tuple[str, str], ...] = (
    ("mcp.server.mcpserver", "MCPServer"),  # mcp >= 2
    ("mcp.server.fastmcp", "FastMCP"),  # mcp < 2
)


def load_server_class(
    import_module: Callable[[str], Any] = importlib.import_module,
) -> Any:
    """Return the MCP server class, or say precisely why there isn't one.

    Split out of ``serve()`` and given an injectable importer because the version
    handling is the part that breaks, and an import inside a ``# pragma: no cover``
    method is a thing no test can reach. The original spelling caught
    ``ImportError`` around ``mcp.server.fastmcp`` and reported "mcp SDK not
    installed" — but ``ModuleNotFoundError`` subclasses ``ImportError``, so on
    mcp 2.x, where that module is gone, it sent the reader to install a package
    they already had.

    The two failures are therefore reported separately: absent, and present but
    exposing neither entry point.
    """
    try:
        import_module("mcp")
    except ImportError as exc:
        raise RuntimeError(
            "mcp SDK not installed. Run `pip install ronin-mcp-servers[mcp]`."
        ) from exc

    for module_name, attribute in SERVER_CLASSES:
        try:
            module = import_module(module_name)
        except ImportError:
            continue
        server_class = getattr(module, attribute, None)
        if server_class is not None:
            return server_class

    expected = ", ".join(f"{module}.{attr}" for module, attr in SERVER_CLASSES)
    raise RuntimeError(
        f"The installed mcp SDK exposes none of: {expected}. It is installed but "
        f"not a version this server supports — reinstall with "
        f"`pip install 'ronin-mcp-servers[mcp]'`."
    )


def _connect_from_env() -> Any:  # pragma: no cover — needs psycopg2 + a DB
    import psycopg2  # type: ignore[import-not-found]

    return psycopg2.connect(os.environ["DATABASE_URL"])


def main() -> None:  # pragma: no cover
    """Entry point: connect using ``DATABASE_URL`` from env and serve over stdio MCP."""
    connection = _connect_from_env()
    PostgresQueryTool(connection=connection).serve()


if __name__ == "__main__":  # pragma: no cover
    main()
