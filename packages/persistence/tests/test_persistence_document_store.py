"""Contract tests for the swappable DocumentStore backends.

- InMemory + JSON-file: full round-trip, isolation, byte-compatible file format.
- Factory / env selection.
- Postgres: SQL contract verified against an injected fake connection (no live
  server). A real-server round-trip runs only when RONIN_TEST_DATABASE_URL is
  set — otherwise skipped and labeled BLOCKED_INFRASTRUCTURE.
"""
from __future__ import annotations

import json
import os

import pytest

from ronin_persistence import (
    DocumentStore,
    InMemoryDocumentStore,
    JsonFileDocumentStore,
    PostgresDocumentStore,
    from_env,
    open_document_store,
)


# --------------------------------------------------------------- in-memory
def test_inmemory_roundtrip_and_empty_default():
    s = InMemoryDocumentStore()
    assert s.load() == {}          # nothing stored yet → {}
    s.save({"a": 1, "b": [1, 2]})
    assert s.load() == {"a": 1, "b": [1, 2]}


def test_inmemory_does_not_alias_caller_state():
    s = InMemoryDocumentStore()
    payload = {"items": {"x": 1}}
    s.save(payload)
    payload["items"]["x"] = 999     # mutate after save
    assert s.load()["items"]["x"] == 1  # persisted copy is unaffected


# --------------------------------------------------------------- json file
def test_jsonfile_missing_reads_empty(tmp_path):
    s = JsonFileDocumentStore(tmp_path / "nope.json")
    assert s.load() == {}


def test_jsonfile_roundtrip_and_creates_parent(tmp_path):
    s = JsonFileDocumentStore(tmp_path / "nested" / "vault.json")
    s.save({"items": {"m1": {"scope": "user"}}, "usage": []})
    assert s.load() == {"items": {"m1": {"scope": "user"}}, "usage": []}


def test_jsonfile_format_is_byte_compatible_with_legacy(tmp_path):
    # The legacy stores wrote json.dumps(payload, indent=2, sort_keys=True).
    p = tmp_path / "models.json"
    JsonFileDocumentStore(p).save({"b": 2, "a": 1})
    assert p.read_text(encoding="utf-8") == json.dumps(
        {"b": 2, "a": 1}, indent=2, sort_keys=True
    )


def test_jsonfile_blank_file_reads_empty(tmp_path):
    p = tmp_path / "empty.json"
    p.write_text("   \n", encoding="utf-8")
    assert JsonFileDocumentStore(p).load() == {}


# --------------------------------------------------------------- factory
def test_open_document_store_selects_backend(tmp_path):
    assert isinstance(open_document_store("v"), InMemoryDocumentStore)
    assert isinstance(
        open_document_store("v", path=tmp_path / "v.json"), JsonFileDocumentStore
    )
    got = open_document_store("v", dsn="postgresql://h/db", connect=lambda dsn: None)
    assert isinstance(got, PostgresDocumentStore)


def test_from_env_prefers_postgres_when_set(tmp_path, monkeypatch):
    monkeypatch.delenv("RONIN_DATABASE_URL", raising=False)
    assert isinstance(from_env("v", path=tmp_path / "v.json"), JsonFileDocumentStore)
    monkeypatch.setenv("RONIN_DATABASE_URL", "postgresql://h/db")
    assert isinstance(from_env("v", path=tmp_path / "v.json"), PostgresDocumentStore)


def test_all_backends_satisfy_the_protocol(tmp_path):
    for s in (
        InMemoryDocumentStore(),
        JsonFileDocumentStore(tmp_path / "x.json"),
        PostgresDocumentStore("postgresql://h/db", "x", connect=lambda dsn: None),
    ):
        assert isinstance(s, DocumentStore)


# --------------------------------------------------------------- validation
def test_postgres_rejects_bad_names_and_tables():
    with pytest.raises(ValueError):
        PostgresDocumentStore("dsn", "not an identifier")
    with pytest.raises(ValueError):
        PostgresDocumentStore("dsn", "vault", table="bad; DROP TABLE x")


# --------------------------------------------------------------- fake DB
class _FakeCursor:
    def __init__(self, conn):
        self._conn = conn
        self._fetch = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self._conn.executed.append((" ".join(sql.split()), params))
        if sql.strip().upper().startswith("SELECT"):
            self._fetch = self._conn.next_row

    def fetchone(self):
        return self._fetch


class _FakeConn:
    def __init__(self, next_row=None):
        self.executed: list[tuple] = []
        self.commits = 0
        self.next_row = next_row

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return _FakeCursor(self)

    def commit(self):
        self.commits += 1


def test_postgres_save_emits_upsert_and_commits():
    conn = _FakeConn()
    store = PostgresDocumentStore(
        "postgresql://h/db", "vault", connect=lambda dsn: conn
    )
    store.save({"items": {"m1": {"scope": "user"}}})
    sqls = [s for s, _ in conn.executed]
    assert any(s.startswith("CREATE TABLE IF NOT EXISTS ronin_documents") for s in sqls)
    upsert = [(s, p) for s, p in conn.executed if s.startswith("INSERT INTO ronin_documents")]
    assert len(upsert) == 1
    sql, params = upsert[0]
    assert "ON CONFLICT (name) DO UPDATE" in sql
    assert params[0] == "vault"
    assert json.loads(params[1]) == {"items": {"m1": {"scope": "user"}}}
    assert conn.commits == 1


def test_postgres_load_returns_row_data_and_empty_when_missing():
    # decoded JSONB (dict) from the driver
    conn = _FakeConn(next_row=({"items": {"a": 1}},))
    store = PostgresDocumentStore("postgresql://h/db", "vault", connect=lambda dsn: conn)
    assert store.load() == {"items": {"a": 1}}
    # raw JSON string is also tolerated
    conn2 = _FakeConn(next_row=(json.dumps({"k": 2}),))
    store2 = PostgresDocumentStore("postgresql://h/db", "v", connect=lambda dsn: conn2)
    assert store2.load() == {"k": 2}
    # no row → {}
    conn3 = _FakeConn(next_row=None)
    store3 = PostgresDocumentStore("postgresql://h/db", "v", connect=lambda dsn: conn3)
    assert store3.load() == {}


def test_postgres_ensure_table_runs_once():
    conn = _FakeConn(next_row=None)
    store = PostgresDocumentStore("postgresql://h/db", "v", connect=lambda dsn: conn)
    store.load()
    store.save({"a": 1})
    store.load()
    ddl = [s for s, _ in conn.executed if s.startswith("CREATE TABLE")]
    assert len(ddl) == 1  # table ensured only on the first operation


# --------------------------------------------------- real server (opt-in)
@pytest.mark.skipif(
    not os.environ.get("RONIN_TEST_DATABASE_URL"),
    reason="BLOCKED_INFRASTRUCTURE: set RONIN_TEST_DATABASE_URL for a live round-trip",
)
def test_postgres_real_roundtrip():  # pragma: no cover - needs a live server
    dsn = os.environ["RONIN_TEST_DATABASE_URL"]
    store = PostgresDocumentStore(dsn, "ronin_persistence_selftest")
    store.save({"hello": "world", "n": 3})
    assert store.load() == {"hello": "world", "n": 3}
