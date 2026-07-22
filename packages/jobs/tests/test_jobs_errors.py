"""Tests for the error taxonomy and redaction."""

from __future__ import annotations

import pytest

from ronin_jobs import (
    PaymentsDisabledError,
    PermissionError,
    ProviderError,
    RateLimitedError,
    RoninError,
    Severity,
    SystemError,
    TimeoutError,
    UserError,
    ValidationError,
    classify,
    is_retryable,
    redact,
)


def test_retryable_flags_by_class() -> None:
    assert UserError().retryable is False
    assert ValidationError().retryable is False
    assert PermissionError().retryable is False
    assert PaymentsDisabledError().retryable is False
    assert RateLimitedError().retryable is True
    assert ProviderError().retryable is True
    assert TimeoutError().retryable is True
    assert SystemError().retryable is True


def test_stable_codes() -> None:
    assert RateLimitedError().code == "rate_limited"
    assert ValidationError().code == "validation_error"
    assert PaymentsDisabledError().code == "payments_disabled"
    assert SystemError().code == "system_error"


def test_validation_is_a_user_error() -> None:
    assert isinstance(ValidationError(), UserError)


def test_severity_levels() -> None:
    assert SystemError().severity is Severity.critical
    assert RateLimitedError().severity is Severity.warning
    assert PaymentsDisabledError().severity is Severity.error


def test_user_message_default_is_safe_and_fixed() -> None:
    err = ProviderError(detail="upstream 500 from internal-host-42")
    # The raw detail must never appear in the user-facing message.
    assert "internal-host-42" not in err.user_message
    assert err.user_message == ProviderError.default_user_message


def test_str_never_leaks_detail() -> None:
    err = SystemError(detail="connection to 10.0.0.5 refused")
    assert "10.0.0.5" not in str(err)


def test_detail_is_redacted_for_sensitive_pairs() -> None:
    err = ProviderError(detail="failed with password=example-not-real value")
    assert "example-not-real" not in err.detail
    assert "[redacted]" in err.detail


def test_redact_masks_long_opaque_blobs() -> None:
    blob = "PLACEHOLDERPLACEHOLDERPLACEHOLDER"
    assert blob not in redact(f"opaque value was {blob}")


def test_redact_empty_string() -> None:
    assert redact("") == ""


def test_classify_passes_through_ronin_error() -> None:
    err = RateLimitedError()
    assert classify(err) is err


def test_classify_wraps_plain_exception_as_system_error() -> None:
    wrapped = classify(RuntimeError("boom"))
    assert isinstance(wrapped, SystemError)
    assert wrapped.retryable is True


def test_classify_redacts_wrapped_detail() -> None:
    wrapped = classify(RuntimeError("api_key=example-value-placeholder"))
    assert "example-value-placeholder" not in wrapped.detail


def test_is_retryable_fail_closed_for_unknown() -> None:
    assert is_retryable(RuntimeError("???")) is True
    assert is_retryable(ValueError("bad")) is True


def test_is_retryable_respects_ronin_flag() -> None:
    assert is_retryable(ValidationError()) is False
    assert is_retryable(TimeoutError()) is True


def test_user_message_override_allowed_but_not_from_detail() -> None:
    err = ProviderError(detail="secret=example-placeholder", user_message="Please retry.")
    assert err.user_message == "Please retry."
    assert "example-placeholder" not in err.detail  # 'secret=' pair redacted


def test_severity_override() -> None:
    err = ProviderError(severity=Severity.info)
    assert err.severity is Severity.info
