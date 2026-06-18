"""Shared test fixtures.

Everything here runs offline: no real sockets, no external services. We build
the relay app in-process with FastAPI's TestClient, and we drive the connector
forwarding logic with a fake local target.
"""

from __future__ import annotations

import pytest

from ronin_relay.config import ConnectorConfig, RelayConfig

# A token that satisfies the minimum length check.
TEST_TOKEN = "test-token-please-change-me-0123456789"


@pytest.fixture
def relay_config() -> RelayConfig:
    return RelayConfig(
        token=TEST_TOKEN,
        rate_limit_max=5,
        rate_limit_window_seconds=60.0,
        request_timeout_seconds=2.0,
    )


@pytest.fixture
def connector_config() -> ConnectorConfig:
    return ConnectorConfig(
        token=TEST_TOKEN,
        relay_url="ws://relay.test/connect",
        target_url="http://127.0.0.1:9999/ronin",
        target_timeout_seconds=2.0,
    )
