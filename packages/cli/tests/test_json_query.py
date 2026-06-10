"""Tests for the JSON path query."""
from __future__ import annotations

import pytest

from ronin_cli.json_query import QueryError, keys_at, query_json

_DATA = {"users": [{"name": "ada", "roles": ["admin", "dev"]}, {"name": "bob"}], "count": 2}


def test_dot_and_bracket_paths() -> None:
    assert query_json(_DATA, "users.0.name") == "ada"
    assert query_json(_DATA, "users[0].name") == "ada"
    assert query_json(_DATA, "users.0.roles.1") == "dev"
    assert query_json(_DATA, "count") == 2


def test_root_returns_all() -> None:
    assert query_json(_DATA, ".") == _DATA
    assert query_json(_DATA, "") == _DATA


def test_missing_raises() -> None:
    with pytest.raises(QueryError):
        query_json(_DATA, "users.9.name")
    with pytest.raises(QueryError):
        query_json(_DATA, "nope")


def test_keys_at() -> None:
    assert keys_at(_DATA) == ["users", "count"]
    assert keys_at(_DATA["users"]) == ["0", "1"]
