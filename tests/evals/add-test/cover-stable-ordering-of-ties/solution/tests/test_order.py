"""Tests for rank.order."""

from rank.order import order


def test_higher_scores_come_first():
    assert order([("a", 1), ("b", 5)]) == ["b", "a"]


def test_entries_with_equal_scores_keep_their_input_order():
    entries = [("a", 5), ("b", 5), ("c", 5), ("d", 9)]
    assert order(entries) == ["d", "a", "b", "c"]
