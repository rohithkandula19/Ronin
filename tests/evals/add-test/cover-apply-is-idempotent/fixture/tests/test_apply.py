"""Tests for header.apply."""

from header.apply import HEADER, apply


def test_the_header_is_prepended():
    assert apply("body\n") == HEADER + "body\n"
