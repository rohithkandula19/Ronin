from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Fresh sqlite DB per test, fresh app instance, demo-mode RoninConfig."""
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    # Make briefings deterministic & offline — the CLI's demo_mode flag is on
    # via the user's RoninConfig when the user uploads no real credentials.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    from csk_api.db import reset_db_for_tests
    reset_db_for_tests(db_url)
    from csk_api.main import make_app
    return TestClient(make_app())


# ---------- /health ----------

def test_health(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


# ---------- /signup ----------

def test_signup_creates_user_and_returns_token(client: TestClient) -> None:
    r = client.post("/signup", json={"email": "alice@example.com"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["email"] == "alice@example.com"
    assert body["api_token"].startswith("csk_")
    assert len(body["api_token"]) > 20


def test_signup_rejects_duplicate_email(client: TestClient) -> None:
    client.post("/signup", json={"email": "alice@example.com"})
    r = client.post("/signup", json={"email": "alice@example.com"})
    assert r.status_code == 400
    assert "already exists" in r.json()["detail"]


# ---------- auth ----------

def test_endpoints_require_auth(client: TestClient) -> None:
    for path in ("/connections", "/briefings"):
        r = client.get(path)
        assert r.status_code == 401


def test_invalid_token_rejected(client: TestClient) -> None:
    r = client.get("/connections", headers={"Authorization": "Bearer csk_not_a_real_token"})
    assert r.status_code == 401


# ---------- /connections ----------

def test_add_and_list_connections(client: TestClient) -> None:
    signup = client.post("/signup", json={"email": "bob@example.com"}).json()
    token = signup["api_token"]
    h = {"Authorization": f"Bearer {token}"}

    r = client.post("/connections", json={"service": "stripe", "secret": "rk_live_xxx"}, headers=h)
    assert r.status_code == 201
    r = client.post("/connections", json={"service": "linear", "secret": "lin_api_xxx"}, headers=h)
    assert r.status_code == 201

    r = client.get("/connections", headers=h)
    assert r.status_code == 200
    services = {c["service"] for c in r.json()}
    assert services == {"stripe", "linear"}


def test_connection_secret_never_returned(client: TestClient) -> None:
    """Connections are listed by service name; the raw secret never leaves the DB."""
    signup = client.post("/signup", json={"email": "carol@example.com"}).json()
    h = {"Authorization": f"Bearer {signup['api_token']}"}
    client.post("/connections", json={"service": "stripe", "secret": "rk_secret_value"}, headers=h)
    r = client.get("/connections", headers=h)
    assert "rk_secret_value" not in r.text


def test_unknown_service_rejected(client: TestClient) -> None:
    signup = client.post("/signup", json={"email": "dave@example.com"}).json()
    h = {"Authorization": f"Bearer {signup['api_token']}"}
    r = client.post("/connections", json={"service": "evil-mutator", "secret": "x"}, headers=h)
    assert r.status_code == 400


def test_reupload_overwrites_connection(client: TestClient) -> None:
    signup = client.post("/signup", json={"email": "eve@example.com"}).json()
    h = {"Authorization": f"Bearer {signup['api_token']}"}
    client.post("/connections", json={"service": "stripe", "secret": "v1"}, headers=h)
    client.post("/connections", json={"service": "stripe", "secret": "v2"}, headers=h)
    services = client.get("/connections", headers=h).json()
    assert len(services) == 1  # uniq constraint held


# ---------- /briefings ----------

def test_briefing_runs_with_no_connections_returns_empty_shape(client: TestClient) -> None:
    """Even a freshly-signed-up user with no integrations gets a (mostly empty) briefing."""
    signup = client.post("/signup", json={"email": "frank@example.com"}).json()
    h = {"Authorization": f"Bearer {signup['api_token']}"}

    r = client.post("/briefings", headers=h)
    assert r.status_code == 201, r.text
    body = r.json()
    assert "Founder briefing" in body["markdown"]
    assert body["mrr_cents"] == 0
    assert body["active_subs"] == 0


def test_briefing_history_grows(client: TestClient) -> None:
    signup = client.post("/signup", json={"email": "grace@example.com"}).json()
    h = {"Authorization": f"Bearer {signup['api_token']}"}

    client.post("/briefings", headers=h)
    client.post("/briefings", headers=h)

    r = client.get("/briefings", headers=h)
    assert r.status_code == 200
    assert len(r.json()) == 2


def test_second_briefing_contains_delta_line(client: TestClient) -> None:
    """The second run should pull in a '_vs <date>: …_' delta line."""
    signup = client.post("/signup", json={"email": "henry@example.com"}).json()
    h = {"Authorization": f"Bearer {signup['api_token']}"}
    client.post("/briefings", headers=h)
    body = client.post("/briefings", headers=h).json()
    assert "vs " in body["markdown"]


# ---------- /briefings/schedule ----------

def test_schedule_round_trip(client: TestClient) -> None:
    signup = client.post("/signup", json={"email": "iris@example.com"}).json()
    h = {"Authorization": f"Bearer {signup['api_token']}"}
    r = client.post(
        "/briefings/schedule",
        json={"cron": "0 9 * * 1", "slack_channel": "#founders", "enabled": True},
        headers=h,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["cron"] == "0 9 * * 1"
    assert body["slack_channel"] == "#founders"
    assert body["enabled"] is True


# ---------- worker tick ----------

def test_worker_tick_runs_due_user(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A user whose schedule was never run shows up as 'due' and the worker produces a briefing."""
    signup = client.post("/signup", json={"email": "jack@example.com"}).json()
    h = {"Authorization": f"Bearer {signup['api_token']}"}
    client.post("/briefings/schedule", json={"cron": "0 9 * * 1", "enabled": True}, headers=h)

    from csk_api.worker import tick
    produced = tick()
    assert produced == 1

    # And it should show up in history now
    r = client.get("/briefings", headers=h)
    assert len(r.json()) == 1
