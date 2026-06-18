"""Runtime configuration, read from environment variables.

Nothing here has a usable default for the token. If RONIN_RELAY_TOKEN is unset
or too short, the process is told to fail closed by the caller. We never invent
a token, because a guessable token would let anyone on the internet reach the
laptop.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# Minimum token length we accept. A short token is easy to brute force, and this
# token is the only thing standing between the public internet and the laptop.
MIN_TOKEN_LENGTH = 24


@dataclass(frozen=True)
class RelayConfig:
    """Configuration for the relay server (the free VM side)."""

    token: str
    # Max phone requests allowed per rolling window, per client identity.
    rate_limit_max: int = 60
    rate_limit_window_seconds: float = 60.0
    # How long the relay waits for the connector to answer one request.
    request_timeout_seconds: float = 30.0


@dataclass(frozen=True)
class ConnectorConfig:
    """Configuration for the laptop connector (the outbound-only side)."""

    token: str
    # The relay websocket URL, for example wss://relay.example.com/connect.
    relay_url: str
    # The ONLY local URL this connector is allowed to forward to. This is the
    # Ronin gateway. The connector forwards nothing else. There is no
    # "run any command" path.
    target_url: str
    # Per request timeout when calling the local target.
    target_timeout_seconds: float = 25.0


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or unsafe."""


def _require_token(raw: str | None) -> str:
    if not raw:
        raise ConfigError(
            "RONIN_RELAY_TOKEN is not set. Refusing to start: an unauthenticated "
            "relay would let anyone reach the laptop. Set a strong random token."
        )
    if len(raw) < MIN_TOKEN_LENGTH:
        raise ConfigError(
            "RONIN_RELAY_TOKEN is too short. Use at least "
            f"{MIN_TOKEN_LENGTH} characters of random data, for example the "
            "output of: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )
    return raw


def relay_config_from_env(env: dict[str, str] | None = None) -> RelayConfig:
    """Build the relay config or raise ConfigError if it would be unsafe."""
    src = env if env is not None else dict(os.environ)
    token = _require_token(src.get("RONIN_RELAY_TOKEN"))
    return RelayConfig(
        token=token,
        rate_limit_max=int(src.get("RONIN_RELAY_RATE_MAX", "60")),
        rate_limit_window_seconds=float(src.get("RONIN_RELAY_RATE_WINDOW", "60")),
        request_timeout_seconds=float(src.get("RONIN_RELAY_REQUEST_TIMEOUT", "30")),
    )


def connector_config_from_env(env: dict[str, str] | None = None) -> ConnectorConfig:
    """Build the connector config or raise ConfigError if it would be unsafe."""
    src = env if env is not None else dict(os.environ)
    return connector_config(
        token=src.get("RONIN_RELAY_TOKEN"),
        relay_url=src.get("RONIN_RELAY_URL"),
        target_url=src.get("RONIN_TARGET_URL"),
        target_timeout_seconds=float(src.get("RONIN_TARGET_TIMEOUT", "25")),
    )


def connector_config(
    token: str | None,
    relay_url: str | None,
    target_url: str | None,
    target_timeout_seconds: float = 25.0,
) -> ConnectorConfig:
    """Build a connector config from explicit values, failing closed if unsafe.

    Used by both the env reader and the "connect" CLI flags so the exact same
    validation runs no matter how the values arrived. The token is required and
    must meet the minimum length; the relay URL and target URL are both
    mandatory because the connector forwards to exactly one configured target
    and nothing else.
    """
    checked_token = _require_token(token)
    if not relay_url:
        raise ConfigError(
            "the relay websocket URL is not set. Pass --relay or set "
            "RONIN_RELAY_URL, for example wss://relay.example.com/connect"
        )
    if not target_url:
        raise ConfigError(
            "the local Ronin gateway URL is not set. Pass --target or set "
            "RONIN_TARGET_URL, for example http://127.0.0.1:8000/webhooks/agent. "
            "The connector only ever forwards to this one address."
        )
    return ConnectorConfig(
        token=checked_token,
        relay_url=relay_url,
        target_url=target_url,
        target_timeout_seconds=target_timeout_seconds,
    )
