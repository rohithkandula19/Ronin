"""Tests for the messaging gateway — generic HTTP webhook to talk to ronin.

These run fully OFFLINE: the demo-mode config + no API key routes run_ask through
the local demo brain, so a posted message really runs the agent and returns a
real, data-grounded reply with zero network. No provider key, no live service.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ronin_cli.config import RoninConfig
from ronin_cli.gateway import make_gateway_app


@pytest.fixture(autouse=True)
def _no_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force the offline demo-brain path: no real provider key anywhere.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


def _client(token: str | None = None) -> TestClient:
    config = RoninConfig(demo_mode=True, provider="anthropic")
    return TestClient(make_gateway_app(config, token=token))


def test_health_reports_state() -> None:
    client = _client()
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["demo_mode"] is True
    assert body["auth"] is False


def test_posted_message_runs_agent_offline_and_replies() -> None:
    """The core proof: POST /message runs the real demo-brain agent (offline)
    and returns a data-grounded reply — not an echo, not a stub."""
    client = _client()
    r = client.post(
        "/message",
        json={"text": "how many active subscriptions do we have?", "session_id": "demo"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"] == "demo"
    assert body["success"] is True
    assert body["demo_mode"] is True
    # The demo brain answers the subscriptions question from the mock data: the
    # reply is real content, not the unrecognized-pattern fallback.
    assert body["reply"]
    assert "[demo brain] I'm a keyword router" not in body["reply"]
    assert "subscription" in body["reply"].lower()


def test_empty_text_is_rejected() -> None:
    client = _client()
    r = client.post("/message", json={"text": "   ", "session_id": "x"})
    assert r.status_code == 400


def test_session_id_defaults_when_omitted() -> None:
    client = _client()
    r = client.post("/message", json={"text": "list our customers"})
    assert r.status_code == 200
    assert r.json()["session_id"] == "default"


def test_session_history_persists_across_messages() -> None:
    """Sessions are real: the second message in a session sees the first turn,
    so the stored history grows (user+assistant per message)."""
    config = RoninConfig(demo_mode=True)
    app = make_gateway_app(config)
    client = TestClient(app)
    store = app.state.session_store

    client.post("/message", json={"text": "list our customers", "session_id": "s1"})
    assert store.turns("s1") == 2  # one user + one assistant turn recorded
    client.post("/message", json={"text": "how many subscriptions?", "session_id": "s1"})
    assert store.turns("s1") == 4
    # A different session is isolated.
    assert store.turns("other") == 0


def test_auth_required_when_token_set() -> None:
    client = _client(token="s3cr3t")
    # No header → 401.
    r = client.post("/message", json={"text": "list our customers", "session_id": "a"})
    assert r.status_code == 401
    # Wrong token → 401.
    r = client.post(
        "/message",
        json={"text": "list our customers", "session_id": "a"},
        headers={"Authorization": "Bearer wrong"},
    )
    assert r.status_code == 401
    # Correct token → 200 and a real reply.
    r = client.post(
        "/message",
        json={"text": "list our customers", "session_id": "a"},
        headers={"Authorization": "Bearer s3cr3t"},
    )
    assert r.status_code == 200
    assert r.json()["reply"]


def test_health_open_when_no_token() -> None:
    # Health is never gated (so a load balancer can probe it).
    client = _client(token="s3cr3t")
    assert client.get("/health").status_code == 200
