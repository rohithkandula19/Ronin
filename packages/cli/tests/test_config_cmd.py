"""Tests for the `ronin config` coercion + parsing helpers."""
from __future__ import annotations

import pytest

from ronin_cli.config_cmd import BadSetting, coerce_value, parse_set


def test_coerce_bool() -> None:
    assert coerce_value("sentinel", "true") is True
    assert coerce_value("sentinel", "off") is False
    with pytest.raises(BadSetting):
        coerce_value("sentinel", "maybe")


def test_coerce_float() -> None:
    assert coerce_value("budget", "0.50") == 0.5
    with pytest.raises(BadSetting):
        coerce_value("budget", "lots")


def test_coerce_clear() -> None:
    assert coerce_value("budget", "-") is None
    assert coerce_value("model", "") is None


def test_coerce_str() -> None:
    assert coerce_value("route_fast", "cerebras:gpt-oss-120b") == "cerebras:gpt-oss-120b"


def test_unknown_field() -> None:
    with pytest.raises(BadSetting):
        coerce_value("nope", "x")


def test_parse_set_forms() -> None:
    assert parse_set("budget=0.50") == ("budget", "0.50")
    assert parse_set("route_fast cerebras:m") == ("route_fast", "cerebras:m")
    with pytest.raises(BadSetting):
        parse_set("justfield")
