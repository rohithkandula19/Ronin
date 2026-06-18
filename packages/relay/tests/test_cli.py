"""Tests for the connect command line parsing.

The "connect" verb is the documented entrypoint:

  ronin-relay connect --relay <url> --target <local-gateway-url> --token <t>

These prove the flags build a valid ConnectorConfig, that flags win over the
environment, that the env vars are used when a flag is omitted, and that the
command still fails closed when the token is missing or weak.
"""

from __future__ import annotations

import pytest

from ronin_relay.__main__ import build_connect_config
from ronin_relay.config import ConfigError

STRONG = "x" * 32
RELAY = "wss://relay.example.com/connect"
TARGET = "http://127.0.0.1:8000/webhooks/agent"


def test_connect_flags_build_config(monkeypatch) -> None:
    # No env at all: everything comes from flags.
    monkeypatch.delenv("RONIN_RELAY_TOKEN", raising=False)
    monkeypatch.delenv("RONIN_RELAY_URL", raising=False)
    monkeypatch.delenv("RONIN_TARGET_URL", raising=False)

    cfg = build_connect_config(
        ["--relay", RELAY, "--target", TARGET, "--token", STRONG]
    )
    assert cfg.relay_url == RELAY
    assert cfg.target_url == TARGET
    assert cfg.token == STRONG


def test_connect_flags_override_env(monkeypatch) -> None:
    monkeypatch.setenv("RONIN_RELAY_TOKEN", "y" * 40)
    monkeypatch.setenv("RONIN_RELAY_URL", "wss://old.example.com/connect")
    monkeypatch.setenv("RONIN_TARGET_URL", "http://127.0.0.1:1111/old")

    cfg = build_connect_config(
        ["--relay", RELAY, "--target", TARGET, "--token", STRONG]
    )
    assert cfg.relay_url == RELAY
    assert cfg.target_url == TARGET
    assert cfg.token == STRONG


def test_connect_falls_back_to_env_when_flag_omitted(monkeypatch) -> None:
    monkeypatch.setenv("RONIN_RELAY_TOKEN", STRONG)
    monkeypatch.setenv("RONIN_RELAY_URL", RELAY)
    monkeypatch.setenv("RONIN_TARGET_URL", TARGET)

    # No flags at all: pull everything from the environment.
    cfg = build_connect_config([])
    assert cfg.relay_url == RELAY
    assert cfg.target_url == TARGET
    assert cfg.token == STRONG


def test_connect_requires_token(monkeypatch) -> None:
    monkeypatch.delenv("RONIN_RELAY_TOKEN", raising=False)
    with pytest.raises(ConfigError):
        build_connect_config(["--relay", RELAY, "--target", TARGET])


def test_connect_rejects_short_token(monkeypatch) -> None:
    monkeypatch.delenv("RONIN_RELAY_TOKEN", raising=False)
    with pytest.raises(ConfigError):
        build_connect_config(
            ["--relay", RELAY, "--target", TARGET, "--token", "short"]
        )


def test_connect_requires_target(monkeypatch) -> None:
    monkeypatch.delenv("RONIN_TARGET_URL", raising=False)
    with pytest.raises(ConfigError):
        build_connect_config(["--relay", RELAY, "--token", STRONG])


def test_connect_requires_relay(monkeypatch) -> None:
    monkeypatch.delenv("RONIN_RELAY_URL", raising=False)
    with pytest.raises(ConfigError):
        build_connect_config(["--target", TARGET, "--token", STRONG])
