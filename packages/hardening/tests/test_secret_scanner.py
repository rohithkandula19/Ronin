from __future__ import annotations

import pytest

from ronin_hardening import (
    SecretLeakDetected,
    SecretLeakScanner,
)


def test_clean_text_passes() -> None:
    scanner = SecretLeakScanner()
    result = scanner.scan("here's a perfectly normal sentence with no keys")
    assert result.flagged is False
    assert result.findings == []
    assert result.redacted == "here's a perfectly normal sentence with no keys"


def test_anthropic_key_detected() -> None:
    scanner = SecretLeakScanner()
    text = "the key is sk-ant-FAKEKEYNOTREALabcdef56789"
    result = scanner.scan(text)
    assert result.flagged is True
    assert "[anthropic-key]" in result.redacted
    assert "sk-ant-api03" not in result.redacted
    assert result.findings[0].label == "anthropic-key"


def test_stripe_restricted_key_detected() -> None:
    scanner = SecretLeakScanner()
    # 20 chars body — long enough for our regex (≥20) but short enough that
    # GitHub's push-protection scanner doesn't classify it as a real Stripe key.
    text = "use rk_test_FAKETESTKEY1234567ab for stripe"
    result = scanner.scan(text)
    assert result.flagged
    assert "[stripe-restricted-key]" in result.redacted
    assert "rk_test_" not in result.redacted


def test_multiple_keys_all_redacted() -> None:
    """Anthropic + Stripe + Linear in one string — all three should be redacted."""
    scanner = SecretLeakScanner()
    # Test fixtures intentionally use short / low-entropy bodies so GitHub's
    # secret-scanning push protection doesn't flag them as real credentials.
    text = (
        "config: ANTHROPIC=sk-ant-FAKETESTKEY1234567ab "
        "STRIPE=rk_test_FAKETESTKEY1234567ab "
        "LINEAR=lin_api_FAKETESTKEY1234567ab"
    )
    result = scanner.scan(text)
    labels = {f.label for f in result.findings}
    assert {"anthropic-key", "stripe-rk", "linear-key"}.issubset(labels)
    assert "sk-ant-" not in result.redacted
    assert "rk_test_" not in result.redacted
    assert "lin_api_" not in result.redacted


def test_jwt_detected() -> None:
    scanner = SecretLeakScanner()
    text = "session: eyJabcdefghijklmn.eyJabcdefghijklmn.signaturepart12345"
    result = scanner.scan(text)
    assert any(f.label == "jwt" for f in result.findings)


def test_aws_access_key_detected() -> None:
    scanner = SecretLeakScanner()
    text = "AWS_ACCESS_KEY=AKIAIOSFODNN7EXAMPLE pls don't commit"
    result = scanner.scan(text)
    assert any(f.label == "aws-akid" for f in result.findings)
    assert "[aws-access-key-id]" in result.redacted


def test_finding_prefix_doesnt_leak_full_secret() -> None:
    """match_prefix is the first 8 chars of the detected secret — used for
    structured logging without echoing the whole credential."""
    scanner = SecretLeakScanner()
    # Linear keys have their own detector that GitHub's push protection
    # doesn't flag at this length; safe fixture.
    text = "lin_api_FAKETESTKEY1234567ab"
    result = scanner.scan(text)
    assert result.findings
    assert len(result.findings[0].match_prefix) == 8
    assert result.findings[0].match_prefix == "lin_api_"


def test_assert_clean_passes_for_safe_text() -> None:
    SecretLeakScanner().assert_clean("nothing to see here")


def test_assert_clean_raises_for_leak() -> None:
    with pytest.raises(SecretLeakDetected) as exc_info:
        SecretLeakScanner().assert_clean("sk-ant-FAKEKEYNOTREALabcdef56789")
    assert "anthropic-key" in str(exc_info.value)
    assert exc_info.value.findings


def test_extra_patterns_are_applied() -> None:
    scanner = SecretLeakScanner(
        extra_patterns=[("internal-id", r"INTERNAL-\d{6}", "[internal-id]")],
    )
    result = scanner.scan("user INTERNAL-123456 needs attention")
    assert result.flagged
    assert "[internal-id]" in result.redacted


def test_empty_input_short_circuits() -> None:
    result = SecretLeakScanner().scan("")
    assert result.flagged is False
    assert result.redacted == ""
