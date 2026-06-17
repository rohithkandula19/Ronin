"""Read-only dashboard endpoints, proven against a temp .ronin home.

Each test writes REAL records through the ronin_cli stores (sessions, faithfulness,
orchestrations, memory, skills) into a temp .ronin home, then asserts the matching
endpoint returns exactly that stored data. A stub returning canned JSON would not
echo the distinctive values written here, so these tests fail a fake.

Fast by construction: no provider, no network, no DB writes for the data path,
just JSON files on disk read back through FastAPI's TestClient.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A temp .ronin home: RONIN_HOME for the project-local stores (sessions,
    faithfulness, orchestrations) and HOME for the user-global stores (memory,
    skills)."""
    monkeypatch.setenv("RONIN_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def client(home: Path) -> TestClient:
    """A standalone app with only the dashboard router (no SaaS DB needed)."""
    from csk_api.dashboard_api import mount_dashboard

    app = FastAPI()
    mount_dashboard(app)
    return TestClient(app)


# ---------- /api/runs : sessions ----------

def test_runs_lists_real_sessions(client: TestClient, home: Path) -> None:
    from ronin_cli.sessions import save_session

    save_session(home, ["USER: fix the parser bug", "ASSISTANT: patched parser.py"],
                 session_id="20260101-120000-aaaaaa", base=home)

    r = client.get("/api/runs")
    assert r.status_code == 200
    sessions = r.json()["sessions"]
    assert len(sessions) == 1
    s = sessions[0]
    assert s["id"] == "20260101-120000-aaaaaa"
    assert s["title"] == "fix the parser bug"  # real title parsed from the stored transcript
    assert s["turns"] == 1
    assert s["kind"] == "session"


def test_runs_empty_state_is_honest(client: TestClient, home: Path) -> None:
    r = client.get("/api/runs")
    assert r.status_code == 200
    body = r.json()
    assert body["sessions"] == []
    assert body["orchestrations"] == []
    assert body["count"] == 0


# ---------- /api/runs/{id} : session transcript ----------

def test_run_detail_returns_full_transcript(client: TestClient, home: Path) -> None:
    from ronin_cli.sessions import save_session

    transcript = ["USER: add retries", "ASSISTANT: added retry loop to client.py"]
    save_session(home, transcript, session_id="sess-detail-1", base=home)

    r = client.get("/api/runs/sess-detail-1")
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "session"
    assert body["transcript"] == transcript  # exact stored transcript, not canned


def test_run_detail_404_for_unknown(client: TestClient, home: Path) -> None:
    r = client.get("/api/runs/does-not-exist")
    assert r.status_code == 404


# ---------- /api/runs/{id} : orchestrator subagent tree ----------

def test_run_detail_returns_subagent_tree(client: TestClient, home: Path) -> None:
    from ronin_cli.run_store import record_orchestration

    record_orchestration(
        "add retry + tests to the http client",
        success=True,
        plan_subtasks=[
            {"id": "t1", "description": "research the client", "assignee": "researcher", "depends_on": []},
            {"id": "t2", "description": "implement retries", "assignee": "implementer", "depends_on": ["t1"]},
        ],
        subtask_results=[
            {"subtask_id": "t1", "assignee": "researcher", "success": True, "output": "client.py uses requests", "error": None, "iterations": 2},
            {"subtask_id": "t2", "assignee": "implementer", "success": True, "output": "added retry loop", "error": None, "iterations": 5},
        ],
        output="Added retries with tests.",
        run_id="orch-tree-1",
        root=home,
    )

    # It shows up in the runs list as an orchestration.
    runs = client.get("/api/runs").json()["orchestrations"]
    assert any(o["id"] == "orch-tree-1" and o["subtasks"] == 2 for o in runs)

    # And the detail carries the real plan + subtask results (the tree).
    r = client.get("/api/runs/orch-tree-1")
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "orchestration"
    assert body["goal"] == "add retry + tests to the http client"
    tree = body["tree"]
    assert [p["id"] for p in tree["plan"]] == ["t1", "t2"]
    assert tree["plan"][1]["depends_on"] == ["t1"]
    assert tree["subtasks"][1]["assignee"] == "implementer"
    assert tree["subtasks"][0]["output"] == "client.py uses requests"


# ---------- /api/faithfulness ----------

def test_faithfulness_returns_real_scores(client: TestClient, home: Path) -> None:
    from ronin_cli.run_store import record_faithfulness

    record_faithfulness(0.91, verdict="grounded", n_sources=3, answer="login calls make_token", root=home)
    record_faithfulness(0.20, verdict="ungrounded", n_sources=1, answer="it calls do_magic()",
                        hallucinated_symbols=["do_magic"], root=home)

    r = client.get("/api/faithfulness")
    assert r.status_code == 200
    body = r.json()
    scores = body["scores"]
    assert len(scores) == 2
    # Newest first: the ungrounded one was written last.
    assert scores[0]["verdict"] == "ungrounded"
    assert scores[0]["score"] == 0.2
    assert scores[0]["hallucinated_symbols"] == ["do_magic"]
    assert scores[1]["verdict"] == "grounded"

    summary = body["summary"]
    assert summary["total"] == 2
    assert summary["grounded"] == 1
    assert summary["ungrounded"] == 1


def test_faithfulness_empty_state(client: TestClient, home: Path) -> None:
    r = client.get("/api/faithfulness")
    assert r.status_code == 200
    body = r.json()
    assert body["scores"] == []
    assert body["summary"]["total"] == 0


# ---------- /api/memory ----------

def test_memory_returns_real_facts(client: TestClient, home: Path) -> None:
    from ronin_cli.memory_store import add_memory

    add_memory("User's name is Rohith")
    add_memory("Prefers Python")

    r = client.get("/api/memory")
    assert r.status_code == 200
    body = r.json()
    texts = [m["text"] for m in body["memories"]]
    assert "User's name is Rohith" in texts
    assert "Prefers Python" in texts
    assert body["count"] == 2


def test_memory_empty_state(client: TestClient, home: Path) -> None:
    r = client.get("/api/memory")
    assert r.status_code == 200
    assert r.json() == {"count": 0, "memories": []}


# ---------- /api/skills ----------

def test_skills_returns_real_skills(client: TestClient, home: Path) -> None:
    from ronin_cli.skills_store import save_skill

    save_skill("cut-release", "Cut a release", "1. bump version in {file}\n2. tag v{version}\n3. push")

    r = client.get("/api/skills")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    skill = body["skills"][0]
    assert skill["name"] == "cut-release"
    assert skill["description"] == "Cut a release"
    assert set(skill["params"]) == {"file", "version"}  # params parsed from the real steps
    assert "tag v{version}" in skill["steps"]


def test_skills_empty_state(client: TestClient, home: Path) -> None:
    r = client.get("/api/skills")
    assert r.status_code == 200
    assert r.json() == {"count": 0, "skills": []}


# ---------- the single-page UI ----------

def test_ui_page_is_self_contained(client: TestClient, home: Path) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    html = r.text
    assert "ronin" in html
    # Offline contract: no external resource URLs (no CDN, no http(s) src/href).
    assert "http://" not in html
    assert "https://" not in html
    assert 'src="//' not in html
    # It wires to the real endpoints.
    assert "/api/runs" in html
    assert "/api/faithfulness" in html
