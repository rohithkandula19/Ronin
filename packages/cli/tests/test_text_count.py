from __future__ import annotations
from ronin_cli.text_count import count_text


def test_counts() -> None:
    r = count_text("hello world\nsecond line\n")
    assert r["lines"] == 2 and r["words"] == 4 and r["characters"] == 24


def test_unicode_bytes() -> None:
    r = count_text("café")           # é is 2 bytes in utf-8
    assert r["characters"] == 4 and r["bytes"] == 5


def test_empty() -> None:
    assert count_text("") == {"lines": 0, "words": 0, "characters": 0, "bytes": 0}
