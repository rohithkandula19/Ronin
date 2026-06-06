"""Tests for JSON structural diff."""
from __future__ import annotations

from ronin_cli.diff_json import diff_json, summarize


def test_added_removed_changed() -> None:
    a = {"name": "ada", "age": 36, "city": "London"}
    b = {"name": "ada", "age": 37, "country": "UK"}
    changes = {(c["path"], c["type"]) for c in diff_json(a, b)}
    assert ("age", "changed") in changes
    assert ("city", "removed") in changes
    assert ("country", "added") in changes


def test_nested_and_lists() -> None:
    a = {"user": {"roles": ["a", "b"]}}
    b = {"user": {"roles": ["a", "c", "d"]}}
    paths = {c["path"]: c["type"] for c in diff_json(a, b)}
    assert paths["user.roles[1]"] == "changed"
    assert paths["user.roles[2]"] == "added"


def test_identical_is_empty() -> None:
    assert diff_json({"x": [1, 2]}, {"x": [1, 2]}) == []


def test_summarize() -> None:
    a = {"x": 1, "y": 2}
    b = {"x": 9, "z": 3}
    s = summarize(diff_json(a, b))
    assert s == {"added": 1, "removed": 1, "changed": 1}
