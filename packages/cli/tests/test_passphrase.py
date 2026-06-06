"""Tests for passphrase generation."""
from __future__ import annotations

from ronin_cli.passphrase import WORDS, entropy_for, passphrase


def test_word_count_and_membership() -> None:
    r = passphrase(words=4)
    parts = r["passphrase"].split("-")
    assert len(parts) == 4
    assert all(p in WORDS for p in parts)
    assert r["words"] == 4


def test_clamping() -> None:
    assert passphrase(words=1)["words"] == 2     # floor
    assert passphrase(words=99)["words"] == 12    # ceil


def test_options() -> None:
    r = passphrase(words=3, separator="_", capitalize=True, add_number=True)
    parts = r["passphrase"].split("_")
    assert len(parts) == 4                        # 3 words + a number
    assert parts[0][0].isupper()
    assert parts[-1].isdigit()


def test_entropy_grows_with_words() -> None:
    assert entropy_for(6) > entropy_for(4) > 0
    # 165-word list: ~7.36 bits/word, so 4 words ~29 bits
    assert 28 < entropy_for(4) < 32
