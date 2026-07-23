"""API v1: auth, worlds, workspaces, memory isolation, audit — end to end."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    db_url = f"sqlite:///{tmp_path / 'v1.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("RONIN_AIOS_DATA", str(tmp_path / "aios"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    from csk_api.db import reset_db_for_tests
    reset_db_for_tests(db_url)
    from csk_api.v1 import context
    context.reset_context_for_tests()
    from csk_api.main import make_app
    return TestClient(make_app())


def _register(client: TestClient, email="alice@example.com") -> str:
    r = client.post("/api/v1/auth/register",
                    json={"email": email, "password": "supersecret1"})
    assert r.status_code == 201, r.text
    return r.json()["token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ------------------------------------------------------------------ auth

def test_register_login_me_logout(client: TestClient):
    token = _register(client)
    me = client.get("/api/v1/auth/me", headers=_auth(token))
    assert me.status_code == 200 and me.json()["email"] == "alice@example.com"

    dup = client.post("/api/v1/auth/register",
                      json={"email": "alice@example.com", "password": "supersecret1"})
    assert dup.status_code == 409

    login = client.post("/api/v1/auth/login",
                        json={"email": "alice@example.com", "password": "supersecret1"})
    assert login.status_code == 200
    bad = client.post("/api/v1/auth/login",
                     json={"email": "alice@example.com", "password": "wrongpassword"})
    assert bad.status_code == 401
    assert bad.json()["detail"] == "invalid credentials"  # no account enumeration

    client.post("/api/v1/auth/logout", headers=_auth(token))
    assert client.get("/api/v1/auth/me", headers=_auth(token)).status_code == 401


def test_password_never_stored_plaintext(client: TestClient):
    _register(client)
    import contextlib
    from csk_api.db import session_scope as _dep
    from csk_api.v1.models import AiosUser
    from sqlalchemy import select
    session_scope = contextlib.contextmanager(_dep)
    with session_scope() as db:
        user = db.execute(select(AiosUser)).scalar_one()
        assert "supersecret1" not in user.password_hash
        assert user.password_hash.startswith("pbkdf2$")


def test_protected_route_requires_token(client: TestClient):
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.get("/api/v1/workspaces").status_code == 401


# ---------------------------------------------------------------- worlds

def test_list_worlds_shows_initial_and_disabled(client: TestClient):
    r = client.get("/api/v1/worlds")
    assert r.status_code == 200
    worlds = {w["id"]: w for w in r.json()["worlds"]}
    assert worlds["coding"]["status"] != "disabled"
    assert worlds["healthcare"]["risk_level"] == "high"
    assert "autonomous_diagnosis" in worlds["healthcare"]["blocked_capabilities"]
    assert worlds["finance"]["status"] == "disabled"


def test_enter_world_validates_and_audits(client: TestClient):
    token = _register(client)
    ws = client.post("/api/v1/workspaces", json={"name": "My Space"}, headers=_auth(token))
    ws_id = ws.json()["id"]

    ok = client.post("/api/v1/worlds/enter", headers=_auth(token), json={
        "workspace_id": ws_id, "world": "coding", "role": "developer",
        "country": "US", "language": "en",
    })
    assert ok.status_code == 200, ok.text

    bad_role = client.post("/api/v1/worlds/enter", headers=_auth(token), json={
        "workspace_id": ws_id, "world": "coding", "role": "astronaut",
        "country": "US", "language": "en",
    })
    assert bad_role.status_code == 422

    disabled = client.post("/api/v1/worlds/enter", headers=_auth(token), json={
        "workspace_id": ws_id, "world": "finance", "role": "member",
        "country": "US", "language": "en",
    })
    assert disabled.status_code == 422

    audit = client.get("/api/v1/audit", headers=_auth(token)).json()["events"]
    actions = {e["action"] for e in audit}
    assert {"auth.register", "workspace.create", "world.enter"} <= actions


# --------------------------------------------------- models / adapters

def test_models_and_adapters_listed(client: TestClient):
    models = client.get("/api/v1/models").json()["models"]
    assert any(m["local"] and m["price_in_per_mtok"] == 0.0 for m in models)
    adapters = client.get("/api/v1/adapters", params={"industry": "coding"}).json()["adapters"]
    v2 = next(a for a in adapters if a["id"] == "ronin-coding-1.5b-v2-iter150")
    # honesty: the shipped adapter is completed+measured, not released
    assert v2["state"] == "completed"
    assert v2["eval_results"][0]["passed"] == 36


# --------------------------------------------- memory + cross-world wall

def test_memory_isolation_across_worlds_via_api(client: TestClient):
    token = _register(client)
    # a health memory in the healthcare world
    client.post("/api/v1/memory", headers=_auth(token), json={
        "text": "patient takes medication for blood pressure",
        "scope": "industry", "world": "healthcare", "sensitivity": "health",
    })
    # a coding memory in the coding world
    client.post("/api/v1/memory", headers=_auth(token), json={
        "text": "the blood pressure tracker module uses pytest",
        "scope": "industry", "world": "coding",
    })
    coding = client.post("/api/v1/memory/recall", headers=_auth(token),
                        json={"query": "blood pressure medication", "world": "coding"}).json()["hits"]
    texts = " ".join(h["text"] for h in coding)
    assert "patient takes medication" not in texts  # health memory did NOT leak

    health = client.post("/api/v1/memory/recall", headers=_auth(token),
                        json={"query": "blood pressure medication", "world": "healthcare"}).json()["hits"]
    assert any("patient takes medication" in h["text"] for h in health)


def test_memory_ownership_isolation(client: TestClient):
    alice = _register(client, "alice@example.com")
    bob = _register(client, "bob@example.com")
    r = client.post("/api/v1/memory", headers=_auth(alice),
                    json={"text": "alice private note about deploys", "scope": "user"})
    item_id = r.json()["id"]
    # bob cannot see or delete alice's memory
    assert client.delete(f"/api/v1/memory/{item_id}", headers=_auth(bob)).status_code == 404
    bob_list = client.get("/api/v1/memory", headers=_auth(bob)).json()["items"]
    assert bob_list == []


def test_openapi_documents_v1(client: TestClient):
    spec = client.get("/openapi.json").json()
    paths = spec["paths"]
    assert "/api/v1/auth/register" in paths
    assert "/api/v1/worlds" in paths
    assert "/api/v1/memory/recall" in paths


def test_cors_allowlist(tmp_path, monkeypatch):
    """CORS is opt-in via CORS_ORIGINS: listed origins are allowed, others are
    not, and no allowlist means no CORS header at all."""
    from fastapi.testclient import TestClient as _TC

    def build(origins: str | None) -> _TC:
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'cors.db'}")
        monkeypatch.setenv("RONIN_AIOS_DATA", str(tmp_path / "aios_cors"))
        if origins is None:
            monkeypatch.delenv("CORS_ORIGINS", raising=False)
        else:
            monkeypatch.setenv("CORS_ORIGINS", origins)
        from csk_api.db import reset_db_for_tests
        reset_db_for_tests(f"sqlite:///{tmp_path / 'cors.db'}")
        from csk_api.v1 import context
        context.reset_context_for_tests()
        from csk_api import config
        config.reset_settings_for_tests()
        from csk_api.main import make_app
        return _TC(make_app())

    allowed = "https://app.example.com"
    c = build(allowed)
    # An allowed origin is echoed back.
    r = c.get("/api/v1/worlds/coding", headers={"Origin": allowed})
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == allowed
    # A non-listed origin gets no allow-origin header.
    r2 = c.get("/api/v1/worlds/coding", headers={"Origin": "https://evil.example.com"})
    assert r2.headers.get("access-control-allow-origin") in (None, "")

    # With no allowlist, no CORS header is emitted even for a plausible origin.
    c2 = build(None)
    r3 = c2.get("/api/v1/worlds/coding", headers={"Origin": allowed})
    assert r3.status_code == 200
    assert r3.headers.get("access-control-allow-origin") in (None, "")
