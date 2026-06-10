"""Tests for the UUID tools."""
from __future__ import annotations

import uuid as _uuid

from ronin_cli.uuid_tools import generate, inspect


def test_v4_random_and_valid() -> None:
    out = generate(4)
    parsed = _uuid.UUID(out["uuid"])
    assert parsed.version == 4


def test_v5_is_deterministic() -> None:
    a = generate(5, name="example.com", namespace="dns")
    b = generate(5, name="example.com", namespace="dns")
    assert a["uuid"] == b["uuid"]  # name-based UUIDs are stable


def test_v5_requires_name() -> None:
    assert "error" in generate(5)


def test_unknown_namespace() -> None:
    assert "error" in generate(5, name="x", namespace="bogus")


def test_unsupported_version() -> None:
    assert "error" in generate(2)


def test_inspect_v1_has_timestamp() -> None:
    out = inspect(str(_uuid.uuid1()))
    assert out["version"] == 1
    assert "timestamp" in out


def test_inspect_invalid() -> None:
    assert "error" in inspect("not-a-uuid")


def test_inspect_nil() -> None:
    assert inspect("00000000-0000-0000-0000-000000000000")["is_nil"] is True
