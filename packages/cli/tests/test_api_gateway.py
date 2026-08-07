"""Contract tests for the scoped local Ronin project gateway."""
from __future__ import annotations

from fastapi.testclient import TestClient

from ronin_cli.api_gateway import make_app
from ronin_cli.api_keys import ApiKeyStore
from ronin_cli.mission_models import MissionSpec
from ronin_cli.mission_store import MissionStore


def test_gateway_exposes_only_safe_scoped_project_metadata(tmp_path) -> None:
    read_key = ApiKeyStore(tmp_path).create("editor", scopes=["read"])
    mission_key = ApiKeyStore(tmp_path).create("ci", scopes=["mission:read"])
    mission = MissionStore(tmp_path).create(MissionSpec(title="Inspect release health", issue_text="private details"))
    client = TestClient(make_app(tmp_path))

    assert client.get("/v1/health").json()["mode"] == "read_only"
    assert client.get("/v1/identity").status_code == 401
    identity = client.get("/v1/identity", headers={"Authorization": f"Bearer {read_key.token}"})
    assert identity.status_code == 200
    assert identity.json()["key_id"] == read_key.record.id

    denied = client.get("/v1/missions", headers={"Authorization": f"Bearer {read_key.token}"})
    assert denied.status_code == 403
    missions = client.get("/v1/missions", headers={"Authorization": f"Bearer {mission_key.token}"})
    assert missions.status_code == 200
    entry = missions.json()["missions"][0]
    assert entry["id"] == mission.id
    assert entry["title"] == "Inspect release health"
    assert "issue_text" not in entry and "artifacts" not in entry


def test_gateway_releases_a_concurrency_lease_after_each_request(tmp_path) -> None:
    key = ApiKeyStore(tmp_path).create("status", scopes=["read"])
    client = TestClient(make_app(tmp_path))

    for _ in range(3):
        response = client.get("/v1/identity", headers={"Authorization": f"Bearer {key.token}"})
        assert response.status_code == 200
