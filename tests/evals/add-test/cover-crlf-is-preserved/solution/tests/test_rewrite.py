"""Tests for eol.rewrite."""

from eol.rewrite import rewrite


def test_a_word_is_replaced():
    assert rewrite(b"one\ntwo\n", b"1", b"one") == b"1\ntwo\n"


def test_crlf_line_endings_survive_a_rewrite():
    got = rewrite(b"one\r\ntwo\r\n", b"1", b"one")
    assert got == b"1\r\ntwo\r\n"
    assert b"\r\n" in got
