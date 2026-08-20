"""Tests for slice_.window."""

from slice_.window import window


def test_a_window_from_the_start():
    assert window(["a", "b", "c"], 0, 2) == ["a", "b"]


def test_a_negative_offset_is_rejected():
    try:
        window(["a", "b", "c"], -1, 2)
    except ValueError as exc:
        assert "-1" in str(exc)
    else:
        raise AssertionError("a negative offset must raise ValueError")
