"""Tests for shorten.text."""

from shorten.text import shorten


def test_a_short_label_is_untouched():
    assert shorten("abc", 10) == "abc"


def test_a_long_ascii_label_is_cut():
    assert shorten("abcdefghij", 6) == "abc..."
