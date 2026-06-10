"""Tests for the hashing tools."""
from __future__ import annotations

from ronin_cli.hash_tools import hash_file, hash_text, verify


def test_known_sha256_of_abc() -> None:
    # well-known vector
    out = hash_text("abc")
    assert out["sha256"] == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_empty_string_md5() -> None:
    assert hash_text("")["md5"] == "d41d8cd98f00b204e9800998ecf8427e"


def test_all_algorithms_present() -> None:
    out = hash_text("ronin")
    assert set(out) == {"md5", "sha1", "sha256", "sha512"}


def test_hash_file_matches_text(tmp_path) -> None:
    p = tmp_path / "f.txt"
    p.write_text("abc")
    assert hash_file(p)["sha256"] == hash_text("abc")["sha256"]


def test_hash_file_missing(tmp_path) -> None:
    assert "error" in hash_file(tmp_path / "nope.txt")


def test_verify_case_insensitive() -> None:
    digests = hash_text("abc")
    assert verify(digests["sha256"].upper(), digests) is True
    assert verify("deadbeef", digests) is False
