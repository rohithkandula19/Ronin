"""Tests for rank.order."""

from rank.order import order


def test_higher_scores_come_first():
    assert order([("a", 1), ("b", 5)]) == ["b", "a"]
