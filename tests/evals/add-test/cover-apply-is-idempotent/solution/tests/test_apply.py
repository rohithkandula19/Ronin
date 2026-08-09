"""Tests for header.apply."""

from header.apply import HEADER, apply


def test_the_header_is_prepended():
    assert apply("body\n") == HEADER + "body\n"


def test_applying_the_header_twice_changes_nothing():
    once = apply("body\n")
    assert apply(once) == once
    assert once.count(HEADER) == 1
