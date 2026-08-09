"""Tests for eol.rewrite."""

from eol.rewrite import rewrite


def test_a_word_is_replaced():
    assert rewrite(b"one\ntwo\n", b"1", b"one") == b"1\ntwo\n"
