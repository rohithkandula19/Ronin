"""The agent webhook gateway: POST a message, the agent runs it, you get a reply.

These exercise the real wiring end-to-end with FastAPI's TestClient and no API
key - the user has no provider credentials stored, so the agent answers from
ronin's offline demo brain (no network egress). The gateway is a generic HTTP
endpoint; no live chat-platform integration is involved.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    # No real provider key -> the agent uses the offline demo brain.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    from csk_api.db import reset_db_for_tests
    reset_db_for_tests(db_url)
    from csk_api.main import make_app
    return TestClient(make_app())


def _auth(client: TestClient, email: str = "agent@example.com") -> dict[str, str]:
    token = client.post("/signup", json={"email": email}).json()["api_token"]
    return {"Authorization": f"Bearer {token}"}


def test_gateway_requires_auth(client: TestClient) -> None:
    r = client.post("/webhooks/agent", json={"message": "hello"})
    assert r.status_code == 401


def test_gateway_rejects_bad_token(client: TestClient) -> None:
    r = client.post(
        "/webhooks/agent",
        json={"message": "hello"},
        headers={"Authorization": "Bearer csk_not_a_real_token"},
    )
    assert r.status_code == 401


def test_gateway_runs_agent_and_returns_a_reply(client: TestClient) -> None:
    h = _auth(client)
    r = client.post("/webhooks/agent", json={"message": "what is my current MRR?"}, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["demo_mode"] is True          # ran offline, no key
    assert body["reply"].strip()              # the agent actually produced an answer
    # The demo brain answers MRR/subscription questions with concrete numbers.
    assert "subscription" in body["reply"].lower() or "$" in body["reply"]


def test_gateway_handles_arbitrary_message(client: TestClient) -> None:
    h = _auth(client)
    r = client.post("/webhooks/agent", json={"message": "hello there"}, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    # Even an unrecognized message comes back with a non-empty, successful reply.
    assert body["ok"] is True and body["reply"].strip()


def test_gateway_rejects_empty_message(client: TestClient) -> None:
    h = _auth(client)
    # An all-whitespace message is a 422 (pydantic) or a clean ok=False - never a 500.
    r = client.post("/webhooks/agent", json={"message": "   "}, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False and "empty" in (body["error"] or "")


def test_gateway_missing_message_field_is_422(client: TestClient) -> None:
    h = _auth(client)
    r = client.post("/webhooks/agent", json={}, headers=h)
    assert r.status_code == 422
