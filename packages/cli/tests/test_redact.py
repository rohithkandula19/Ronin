"""Tests for secret/PII redaction."""
from __future__ import annotations

from ronin_cli.redact import redact_text


def test_redacts_email_and_ip() -> None:
    r = redact_text("contact ada@example.com from 192.168.1.50 please")
    assert "[REDACTED-EMAIL]" in r["text"] and "[REDACTED-IPV4]" in r["text"]
    assert "ada@example.com" not in r["text"]


def test_redacts_tokens() -> None:
    text = "key sk-abcdefghijklmnopqrstuvwxyz and ghp_" + "A" * 36
    r = redact_text(text)
    assert "[REDACTED-OPENAI_KEY]" in r["text"]
    assert "[REDACTED-GITHUB_TOKEN]" in r["text"]


def test_counts_and_total() -> None:
    r = redact_text("a@b.com c@d.com 10.0.0.1")
    assert r["redactions"]["EMAIL"] == 2
    assert r["redactions"]["IPV4"] == 1
    assert r["total"] == 3


def test_clean_text_untouched() -> None:
    r = redact_text("just a normal sentence with no secrets")
    assert r["total"] == 0 and "REDACTED" not in r["text"]
