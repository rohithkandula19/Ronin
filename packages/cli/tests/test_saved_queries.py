from __future__ import annotations

from pathlib import Path

import pytest

from ronin_cli.saved_queries import QueryStore, SavedQuery


def test_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "queries.toml"
    store = QueryStore()
    store.add("churn", "Which customers churned this month?", "weekly check")
    store.add("mrr", "What is our MRR right now?")
    store.save(path)

    loaded = QueryStore.load(path)
    assert sorted(loaded.queries.keys()) == ["churn", "mrr"]
    assert loaded.get("churn").query == "Which customers churned this month?"
    assert loaded.get("churn").description == "weekly check"


def test_invalid_name_rejected() -> None:
    store = QueryStore()
    with pytest.raises(ValueError, match="invalid name"):
        store.add("has spaces", "x")
    with pytest.raises(ValueError, match="invalid name"):
        store.add("has/slash", "x")


def test_load_nonexistent_returns_empty(tmp_path: Path) -> None:
    store = QueryStore.load(tmp_path / "nope.toml")
    assert store.queries == {}


def test_remove_idempotent() -> None:
    store = QueryStore()
    store.add("foo", "bar")
    assert store.remove("foo") is True
    assert store.remove("foo") is False


def test_get_missing_raises() -> None:
    store = QueryStore()
    with pytest.raises(KeyError):
        store.get("missing")


def test_list_all_sorted() -> None:
    store = QueryStore()
    store.add("z-query", "z")
    store.add("a-query", "a")
    names = [q.name for q in store.list_all()]
    assert names == ["a-query", "z-query"]
