"""Tests for JSON schema inference + leaf-path discovery."""
from __future__ import annotations

from ronin_cli.schema_infer import infer_schema, leaf_paths


def test_infer_scalars() -> None:
    assert infer_schema("x") == {"type": "string"}
    assert infer_schema(5) == {"type": "integer"}
    assert infer_schema(5.5) == {"type": "number"}
    assert infer_schema(True) == {"type": "boolean"}
    assert infer_schema(None) == {"type": "null"}


def test_infer_object_and_array() -> None:
    s = infer_schema({"name": "a", "age": 3, "tags": ["x", "y"]})
    assert s["type"] == "object"
    assert s["properties"]["name"] == {"type": "string"}
    assert s["properties"]["age"] == {"type": "integer"}
    assert s["properties"]["tags"] == {"type": "array", "items": {"type": "string"}}


def test_leaf_paths() -> None:
    data = {"user": {"name": "ada", "id": 1}, "posts": [{"title": "hi"}]}
    paths = leaf_paths(data)
    assert "user.name" in paths and "user.id" in paths and "posts.0.title" in paths


def test_leaf_paths_respects_limit() -> None:
    big = {str(i): i for i in range(100)}
    assert len(leaf_paths(big, limit=10)) == 10
