"""Tests for settings.lookup."""

from settings.lookup import get


def test_a_known_key_is_returned():
    assert get({"a": "1"}, "a") == "1"


def test_unknown_key_error_names_the_key_and_the_alternatives():
    try:
        get({"alpha": "1", "beta": "2"}, "gamma")
    except KeyError as exc:
        message = str(exc)
    else:
        raise AssertionError("an unknown key must raise KeyError")
    assert "gamma" in message
    assert "alpha" in message
    assert "beta" in message
