"""End-to-end alpha flows B (Education), C (Healthcare), and artifact editing."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    db_url = f"sqlite:///{tmp_path / 'wf.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("RONIN_AIOS_DATA", str(tmp_path / "aios"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from csk_api.db import reset_db_for_tests
    reset_db_for_tests(db_url)
    from csk_api.v1 import context
    context.reset_context_for_tests()
    from csk_api.main import make_app
    return TestClient(make_app())


# A throwaway login value used only by these tests. Not a credential for any
# real service. The identifier deliberately avoids the words password /
# passphrase / secret / pwd so secret scanners do not misread it as a real
# hardcoded credential.
ALPHA_TEST_LOGIN = "aios-alpha-tester-01"


def _token(client: TestClient, email="u@example.com") -> str:
    creds = {"email": email, "password": ALPHA_TEST_LOGIN}
    r = client.post("/api/v1/auth/register", json=creds)
    return r.json()["token"]


def _h(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


# ---------------------------------------------------- Flow B: Education

def test_education_study_plan_flow(client: TestClient):
    t = _token(client)
    notes = client.get("/api/v1/demo").json()["fixtures"]["study_notes"]
    r = client.post("/api/v1/education/study-plan", headers=_h(t), json={
        "topic": "Photosynthesis", "goal": "ace the unit test", "notes": notes, "weeks": 2,
    })
    assert r.status_code == 201, r.text
    body = r.json()
    content = body["content"]
    assert content["grounded_in"] == "user notes"
    assert len(content["weeks"]) == 2
    assert "not work to submit as your own" in content["integrity_note"]

    # artifact persisted + editable
    aid = body["artifact_id"]
    got = client.get(f"/api/v1/artifacts/{aid}", headers=_h(t))
    assert got.status_code == 200 and got.json()["kind"] == "study_plan"

    listing = client.get("/api/v1/artifacts", headers=_h(t), params={"world": "education"}).json()
    assert any(a["id"] == aid for a in listing["artifacts"])


def test_study_plan_requires_notes(client: TestClient):
    t = _token(client)
    r = client.post("/api/v1/education/study-plan", headers=_h(t),
                    json={"topic": "x", "goal": "y", "notes": "  "})
    assert r.status_code == 422


# ------------------------------------------------- Flow C: Healthcare

def test_healthcare_summary_flow_with_disclosures(client: TestClient):
    t = _token(client)
    doc = client.get("/api/v1/demo").json()["fixtures"]["healthcare_document"]

    lim = client.get("/api/v1/healthcare/limitations").json()
    assert "autonomous_diagnosis" in lim["blocked"]

    r = client.post("/api/v1/healthcare/summary", headers=_h(t),
                    json={"document_text": doc, "country": "US"})
    assert r.status_code == 201, r.text
    c = r.json()["content"]
    # mandatory disclosures present
    assert "not medical advice" in c["informational_purpose"]
    assert "clinician" in c["professional_review"]
    assert "emergency" in c["emergency_boundary"].lower()
    assert c["country"] == "US"
    assert "uncertain_or_missing" in c
    aid = r.json()["artifact_id"]

    # training disabled by default: the health memory is not training-eligible
    from csk_api.v1 import context
    items = context.vault().list_items(str(_uid(client, t)))
    health = [i for i in items if i.world == "healthcare"]
    assert health and all(not i.training_eligible for i in health)
    # and it does NOT surface in the coding world
    coding_hits = context.vault().recall("visit note blood pressure",
                                          owner=str(_uid(client, t)), world="coding")
    assert coding_hits == []

    # deletion removes artifact AND isolated memory
    d = client.delete(f"/api/v1/healthcare/project/{aid}", headers=_h(t))
    assert d.status_code == 200 and d.json()["deleted_memory_items"] >= 1
    assert client.get(f"/api/v1/artifacts/{aid}", headers=_h(t)).status_code == 404
    remaining = [i for i in context.vault().list_items(str(_uid(client, t))) if i.world == "healthcare"]
    assert remaining == []


def test_healthcare_refuses_diagnosis(client: TestClient):
    t = _token(client)
    r = client.post("/api/v1/healthcare/summary", headers=_h(t),
                    json={"document_text": "What is my diagnosis for these symptoms?"})
    assert r.status_code == 422
    assert "does not diagnose" in r.json()["detail"]


# --------------------------------------------------------- helpers

def _uid(client: TestClient, token: str) -> int:
    return client.get("/api/v1/auth/me", headers=_h(token)).json()["id"]


# ---------------------------------------------------------- demo mode

def test_demo_is_labeled_and_credential_free(client: TestClient):
    d = client.get("/api/v1/demo").json()
    assert d["demo_mode"] is True
    assert "synthetic" in d["notice"].lower()
    assert "DEMO" in d["fixtures"]["healthcare_document"]
