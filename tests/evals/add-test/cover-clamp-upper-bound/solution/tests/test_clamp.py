"""Tests for bounds.clamp."""

from bounds.clamp import clamp


def test_clamp_raises_the_lower_bound():
    assert clamp(-5, 0, 10) == 0


def test_clamp_leaves_an_in_range_value_alone():
    assert clamp(4, 0, 10) == 4


def test_clamp_caps_at_the_upper_bound():
    assert clamp(99, 0, 10) == 10
