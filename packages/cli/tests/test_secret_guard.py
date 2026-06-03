"""Tests for the secret-leak write guard."""
from __future__ import annotations

from ronin_cli.secret_guard import scan_secrets, secret_warning


def test_clean_content() -> None:
    assert scan_secrets("def f():\n    return 1\n") == []
    assert secret_warning("nothing secret here") is None


def test_detects_aws_key() -> None:
    text = 'AWS_KEY = "AKIAIOSFODNN7EXAMPLE"'
    labels = scan_secrets(text)
    assert any("aws" in l.lower() for l in labels)
    w = secret_warning(text)
    assert w is not None and "secret" in w.lower()


def test_detects_github_token() -> None:
    text = "token: ghp_" + "a" * 36
    assert any("github" in l.lower() for l in scan_secrets(text))


def test_empty_is_clean() -> None:
    assert scan_secrets("") == []
    assert secret_warning("") is None
