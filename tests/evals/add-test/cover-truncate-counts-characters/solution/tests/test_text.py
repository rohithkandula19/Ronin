"""Tests for shorten.text."""

from shorten.text import shorten


def test_a_short_label_is_untouched():
    assert shorten("abc", 10) == "abc"


def test_a_long_ascii_label_is_cut():
    assert shorten("abcdefghij", 6) == "abc..."


def test_a_multibyte_label_is_cut_by_characters():
    label = "\u00e9\u00e9\u00e9\u00e9\u00e9\u00e9\u00e9\u00e9"
    got = shorten(label, 6)
    assert got == "\u00e9\u00e9\u00e9..."
    assert len(got) == 6
    got.encode("utf-8").decode("utf-8")
