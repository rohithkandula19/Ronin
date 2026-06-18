"""Tests for auth and rate limiting, and for config failing closed."""

from __future__ import annotations

import pytest

from ronin_relay.config import (
    ConfigError,
    connector_config_from_env,
    relay_config_from_env,
)
from ronin_relay.security import RateLimiter, extract_bearer, token_matches


def test_extract_bearer() -> None:
    assert extract_bearer("Bearer abc123") == "abc123"
    assert extract_bearer("bearer abc123") == "abc123"
    assert extract_bearer("Basic abc123") is None
    assert extract_bearer(None) is None
    assert extract_bearer("garbage") is None


def test_token_matches() -> None:
    assert token_matches("secret", "secret") is True
    assert token_matches("secret", "other") is False
    assert token_matches(None, "secret") is False
    assert token_matches("", "secret") is False


def test_rate_limiter_allows_then_blocks() -> None:
    limiter = RateLimiter(max_events=2, window_seconds=10.0)
    assert limiter.allow("k", now=0.0) is True
    assert limiter.allow("k", now=1.0) is True
    # Third within the window is blocked.
    assert limiter.allow("k", now=2.0) is False
    # After the window slides past, allowed again.
    assert limiter.allow("k", now=20.0) is True


def test_rate_limiter_is_per_identity() -> None:
    limiter = RateLimiter(max_events=1, window_seconds=10.0)
    assert limiter.allow("a", now=0.0) is True
    assert limiter.allow("a", now=1.0) is False
    # A different identity has its own budget.
    assert limiter.allow("b", now=1.0) is True


def test_relay_config_requires_token() -> None:
    with pytest.raises(ConfigError):
        relay_config_from_env(env={})


def test_relay_config_rejects_short_token() -> None:
    with pytest.raises(ConfigError):
        relay_config_from_env(env={"RONIN_RELAY_TOKEN": "short"})


def test_relay_config_accepts_strong_token() -> None:
    cfg = relay_config_from_env(
        env={"RONIN_RELAY_TOKEN": "x" * 32}
    )
    assert cfg.token == "x" * 32


def test_connector_config_requires_urls() -> None:
    with pytest.raises(ConfigError):
        connector_config_from_env(env={"RONIN_RELAY_TOKEN": "x" * 32})
    with pytest.raises(ConfigError):
        connector_config_from_env(
            env={"RONIN_RELAY_TOKEN": "x" * 32, "RONIN_RELAY_URL": "ws://r/connector"}
        )
