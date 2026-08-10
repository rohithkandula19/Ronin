"""Tests for slice_.window."""

from slice_.window import window


def test_a_window_from_the_start():
    assert window(["a", "b", "c"], 0, 2) == ["a", "b"]
