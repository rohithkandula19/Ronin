"""Tests for wordcount.count."""

from wordcount.count import count_words


def test_two_words():
    assert count_words("a b") == 2
