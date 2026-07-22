"""Swappable single-document persistence for Ronin AI OS stores.

Every Ronin store (model/adapter registry, vault, artifacts, …) persists its
whole collection as one JSON payload. This module makes *where* that payload
lives swappable behind one tiny interface, so the same store code runs on a
local JSON file today and a managed PostgreSQL tomorrow — with no change to the
store logic and no new hard dependency on a database driver.

Interface
---------
A ``DocumentStore`` owns exactly one JSON document::

    load() -> dict          # {} when nothing has been written yet
    save(data: dict) -> None

Backends
--------
- ``InMemoryDocumentStore``  — process-local; for tests and demo mode.
- ``JsonFileDocumentStore``  — byte-for-byte the legacy behavior
  (``json.dumps(..., indent=2, sort_keys=True)`` to a path). Default everywhere.
- ``PostgresDocumentStore``  — one ``JSONB`` row keyed by document name in a
  ``ronin_documents`` table. Imports ``psycopg`` lazily, so the core runtime
  never needs a database driver installed.

Selection is centralized in ``open_document_store`` / ``from_env`` so a single
env switch (``RONIN_DATABASE_URL``) moves a deployment from files to Postgres.

Design rules (each tested):
- Fail-safe empty: a missing document reads as ``{}`` — never raises.
- Byte-compatible files: the JSON-file backend writes exactly what the stores
  wrote before, so existing on-disk data and tests keep working.
- No implicit database: Postgres is opt-in and its driver import is lazy.
- No secret logging: connection strings are never echoed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "DocumentStore",
    "InMemoryDocumentStore",
    "JsonFileDocumentStore",
    "PostgresDocumentStore",
    "open_document_store",
    "from_env",
]


@runtime_checkable
class DocumentStore(Protocol):
    """Persistence for exactly one JSON document (a store's whole payload)."""

    def load(self) -> dict[str, Any]:
        """Return the stored document, or ``{}`` if nothing is stored yet."""
        ...

    def save(self, data: dict[str, Any]) -> None:
        """Persist ``data`` as the whole document, replacing any prior value."""
        ...


class InMemoryDocumentStore:
    """Process-local document store. Deep-copies through JSON so callers can't
    mutate persisted state by holding a reference — matching the file/DB
    backends, which always round-trip through serialization."""

    def __init__(self, initial: dict[str, Any] | None = None) -> None:
        self._raw: str | None = json.dumps(initial) if initial else None

    def load(self) -> dict[str, Any]:
        return json.loads(self._raw) if self._raw else {}

    def save(self, data: dict[str, Any]) -> None:
        self._raw = json.dumps(data)


class JsonFileDocumentStore:
    """The legacy behavior: one pretty-printed JSON file at ``path``.

    Writes ``indent=2, sort_keys=True`` exactly as the stores did inline, so
    files produced before and after this refactor are identical."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        text = self.path.read_text(encoding="utf-8")
        if not text.strip():
            return {}
        return json.loads(text)

    def save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(data, indent=2, sort_keys=True), encoding="utf-8"
        )


class PostgresDocumentStore:
    """One ``JSONB`` row per document in a shared table.

    Table (created on first use if ``ensure_table``):

        CREATE TABLE ronin_documents (
            name       TEXT PRIMARY KEY,
            data       JSONB NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )

    ``psycopg`` (v3) is imported lazily so importing this module never requires
    a database driver. Install the driver via the package's ``postgres`` extra.

    A ``connect`` callable may be injected (returning a DB-API connection) — the
    default builds one from the DSN via ``psycopg.connect``. Injection keeps the
    SQL contract unit-testable without a live server.
    """

    _TABLE_DDL = (
        "CREATE TABLE IF NOT EXISTS {table} ("
        "name TEXT PRIMARY KEY, "
        "data JSONB NOT NULL, "
        "updated_at TIMESTAMPTZ NOT NULL DEFAULT now())"
    )
    _UPSERT = (
        "INSERT INTO {table} (name, data, updated_at) "
        "VALUES (%s, %s::jsonb, now()) "
        "ON CONFLICT (name) DO UPDATE SET data = EXCLUDED.data, updated_at = now()"
    )
    _SELECT = "SELECT data FROM {table} WHERE name = %s"

    def __init__(
        self,
        dsn: str,
        name: str,
        *,
        table: str = "ronin_documents",
        connect: Any = None,
        ensure_table: bool = True,
    ) -> None:
        if not name or not name.isidentifier():
            raise ValueError("document name must be a non-empty identifier")
        if not _is_safe_identifier(table):
            raise ValueError("table must be a plain SQL identifier")
        self._dsn = dsn
        self._name = name
        self._table = table
        self._connect = connect
        self._ensured = not ensure_table

    # -- connection -------------------------------------------------------
    def _open(self) -> Any:
        if self._connect is not None:
            return self._connect(self._dsn)
        try:
            import psycopg  # lazy: only needed when Postgres is actually used
        except ModuleNotFoundError as exc:  # pragma: no cover - env-dependent
            raise RuntimeError(
                "PostgresDocumentStore needs psycopg — install the "
                "'postgres' extra (pip install 'ronin-persistence[postgres]')"
            ) from exc
        return psycopg.connect(self._dsn)

    def _ensure(self, cur: Any) -> None:
        if self._ensured:
            return
        cur.execute(self._TABLE_DDL.format(table=self._table))
        self._ensured = True

    # -- DocumentStore ----------------------------------------------------
    def load(self) -> dict[str, Any]:
        with self._open() as conn:
            with conn.cursor() as cur:
                self._ensure(cur)
                cur.execute(self._SELECT.format(table=self._table), (self._name,))
                row = cur.fetchone()
        if not row or row[0] is None:
            return {}
        value = row[0]
        # psycopg returns JSONB as a decoded object; tolerate a raw string too.
        return json.loads(value) if isinstance(value, str) else dict(value)

    def save(self, data: dict[str, Any]) -> None:
        payload = json.dumps(data, sort_keys=True)
        with self._open() as conn:
            with conn.cursor() as cur:
                self._ensure(cur)
                cur.execute(
                    self._UPSERT.format(table=self._table), (self._name, payload)
                )
            commit = getattr(conn, "commit", None)
            if callable(commit):
                commit()


def _is_safe_identifier(value: str) -> bool:
    """A conservative SQL identifier check (table names are never user input,
    but we still refuse anything that isn't a plain identifier)."""
    return bool(value) and value.replace("_", "a").isalnum() and value[0].isalpha()


def open_document_store(
    name: str,
    *,
    path: str | Path | None = None,
    dsn: str | None = None,
    connect: Any = None,
) -> DocumentStore:
    """Pick a backend explicitly. A ``dsn`` selects Postgres; otherwise a
    ``path`` selects the JSON file; with neither, an in-memory store."""
    if dsn:
        return PostgresDocumentStore(dsn, name, connect=connect)
    if path is not None:
        return JsonFileDocumentStore(path)
    return InMemoryDocumentStore()


def from_env(
    name: str,
    *,
    path: str | Path | None = None,
    env_var: str = "RONIN_DATABASE_URL",
) -> DocumentStore:
    """Choose a backend from the environment: if ``env_var`` is set, use
    Postgres; otherwise fall back to the JSON file at ``path`` (or in-memory).

    This is the single switch that moves a deployment from files to Postgres
    without touching store code."""
    dsn = os.environ.get(env_var, "").strip()
    return open_document_store(name, path=path, dsn=dsn or None)
