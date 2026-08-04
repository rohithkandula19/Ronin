"""Coverage for secret detection across credential types."""
from __future__ import annotations

from pathlib import Path

import pytest

from ronin_cli.secret_guard import scan_secrets
from ronin_cli.secret_scan import scan_repo


@pytest.mark.parametrize("text", [
    'k = "ghp_' + "a" * 36 + '"',                     # github PAT
    'aws = "' + "AKIA" + 'ZX7Q2RSTUV3BWXYC"',        # aws access key id
    'stripe = "sk_live_' + "b" * 24 + '"',            # stripe live
    'stripe = "sk_test_' + "c" * 24 + '"',            # stripe test
])
def test_detects_credential_types(text: str) -> None:
    assert scan_secrets(text), f"should flag: {text[:20]}"


def test_clean_code_not_flagged() -> None:
    assert scan_secrets("def add(a, b):\n    return a + b\n") == []


def test_placeholder_suppressed() -> None:
    assert scan_secrets('AKIAIOSFODNN7EXAMPLE') == []
    assert scan_secrets('key = "YOUR_API_KEY_HERE"') == []


def test_allow_pragma_suppresses_any() -> None:
    line = 'token = "ghp_' + "z" * 36 + '"'
    assert scan_secrets(line)                          # flagged on its own
    assert scan_secrets(line + "  # ronin:allow-secret") == []


def test_empty_and_none_safe() -> None:
    assert scan_secrets("") == []


def test_multiple_secrets_all_reported() -> None:
    text = ('a = "' + "AKIA" + 'ZX7Q2RSTUV3BWXYC"\n'
            'b = "ghp_' + "q" * 36 + '"\n')
    labels = scan_secrets(text)
    assert len(labels) >= 2


def test_repository_scan_has_no_literal_credentials() -> None:
    root = Path(__file__).resolve().parents[3]
    assert scan_repo(root) == []
