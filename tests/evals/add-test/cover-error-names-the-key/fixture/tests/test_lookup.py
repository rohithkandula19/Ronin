"""Tests for settings.lookup."""

from settings.lookup import get


def test_a_known_key_is_returned():
    assert get({"a": "1"}, "a") == "1"
