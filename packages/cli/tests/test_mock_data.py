"""Tests for mock-data generation."""
from __future__ import annotations

from ronin_cli.mock_data import generate_mock, generate_records


def test_field_name_aware_values() -> None:
    schema = {"type": "object", "properties": {
        "email": {"type": "string"}, "city": {"type": "string"},
        "age": {"type": "integer"}, "active": {"type": "boolean"}}}
    rec = generate_mock(schema)
    assert "@" in rec["email"]
    assert rec["city"] == "London"
    assert rec["age"] == 1 and rec["active"] is True


def test_enum_uses_first() -> None:
    assert generate_mock({"type": "string", "enum": ["a", "b"]}) == "a"


def test_nested_object_and_array() -> None:
    schema = {"type": "object", "properties": {
        "user": {"type": "object", "properties": {"name": {"type": "string"}}},
        "tags": {"type": "array", "items": {"type": "string"}}}}
    rec = generate_mock(schema)
    assert rec["user"]["name"] == "Ada Lovelace"
    assert rec["tags"] == ["string"]


def test_generate_records_distinct_ids_emails() -> None:
    schema = {"type": "object", "properties": {
        "id": {"type": "string"}, "email": {"type": "string"}}}
    recs = generate_records(schema, count=3)
    assert [r["id"] for r in recs] == ["1", "2", "3"]
    assert recs[0]["email"] == "user1@example.com" and recs[2]["email"] == "user3@example.com"
