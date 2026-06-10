"""Tests for LICENSE generation."""
from __future__ import annotations

from ronin_cli.license_gen import available, license_text


def test_mit_fills_name_and_year() -> None:
    t = license_text("mit", "Ada Lovelace", "2026")
    assert "MIT License" in t
    assert "Copyright (c) 2026 Ada Lovelace" in t


def test_aliases() -> None:
    assert "BSD 3-Clause" in license_text("bsd", "X", "2026")
    assert "public domain" in license_text("public-domain", "X", "2026")


def test_unknown_is_none() -> None:
    assert license_text("nope") is None


def test_available_includes_common() -> None:
    a = available()
    assert "mit" in a and "isc" in a and "unlicense" in a
