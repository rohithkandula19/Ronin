"""Tests for the self-contained web dashboard (``ronin ui``).

These are FAST and fully offline (FastAPI ``TestClient``, no network, no keys).
They assert two kinds of thing:

1. The page is genuinely self-contained: ``GET /`` returns HTML with NO external
   resource URLs and its JavaScript ``fetch``es only this app's own ``/ui/*``
   endpoints.
2. The endpoints surface REAL stored data, not canned JSON. Each data test
   writes real records via ronin's own stores into a temp ``.ronin`` home and a
   temp project, then asserts the API reads them back. A stub that returned a
   fixed payload (ignoring what was written) would fail these.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """App instance with a temp .ronin home and temp project CWD.

    ``RONIN_HOME`` redirects the run store + memory; the working directory
    redirects sessions + skills (both are project-relative). Everything the
    dashboard reads is therefore isolated to ``tmp_path``.
    """
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("RONIN_HOME", str(home))

    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)

    from csk_api.db import reset_db_for_tests
    reset_db_for_tests(db_url)
    from csk_api.main import make_app
    return TestClient(make_app())


# ---------------------------------------------------------------------------
# Self-contained / offline page
# ---------------------------------------------------------------------------

def test_root_returns_html(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    body = r.text
    assert "<!DOCTYPE html>" in body
    assert "ronin" in body.lower()


def test_page_has_no_external_resource_urls(client: TestClient) -> None:
    """No CDN, no web fonts, no remote images/scripts/styles: the page must work
    fully offline. The only URLs allowed are relative ``/ui/...`` API paths."""
    body = client.get("/").text

    # No absolute http(s) URLs anywhere in the page.
    abs_urls = re.findall(r"https?://[^\s\"')]+", body)
    assert abs_urls == [], f"page references external URLs: {abs_urls}"

    # No external-resource attributes / imports of any kind.
    assert "<script src" not in body.lower()
    assert "<link " not in body.lower()
    assert "@import" not in body.lower()
    assert "url(http" not in body.lower()
    assert "//cdn" not in body.lower()
    # No remote-image src either.
    for m in re.findall(r'src\s*=\s*"([^"]*)"', body, re.IGNORECASE):
        assert not m.startswith(("http://", "https://", "//"))


def test_js_calls_only_local_endpoints(client: TestClient) -> None:
    """Every fetch() target the page uses is a relative /ui/... path on this
    same app — the JS never talks to any other host."""
    body = client.get("/").text

    fetches = re.findall(r"fetch\(\s*([^,)]+)", body)
    assert fetches, "expected the page to call fetch()"
    for f in fetches:
        # Allowed: a relative "/ui/..." string, or a variable built from the
        # API map (which only holds "/ui/..." paths — asserted below).
        assert "http://" not in f and "https://" not in f, f"non-local fetch: {f}"

    # The API map the JS builds its URLs from must only contain "/ui/..." paths.
    api_paths = re.findall(r'"(/[a-zA-Z0-9/_-]+)"', body)
    ui_paths = [p for p in api_paths if p.startswith("/ui")]
    assert ui_paths, "expected /ui/* endpoints referenced in the page"
    # Any absolute path the JS references for the API must live under /ui.
    referenced = re.findall(r':\s*"(/[^"]+)"', body)
    for p in referenced:
        if p == "/":
            continue
        assert p.startswith("/ui"), f"page references a non-/ui API path: {p}"


# ---------------------------------------------------------------------------
# Real data: runs (orchestration tree + faithfulness)
# ---------------------------------------------------------------------------

def _write_run(goal: str, *, run_id: str = "run-1", success: bool = True) -> dict:
    """Write a REAL orchestration run via the run store and return the record."""
    from ronin_cli.run_store import record_from_outcome, save_run

    class _Outcome:
        def __init__(self) -> None:
            self.success = success
            self.output = "session.token is refreshed by refresh_token in auth.py"
            self.error = None
            self.plan_subtasks = [
                {"id": "t1", "description": "investigate the auth module",
                 "assignee": "researcher", "depends_on": []},
                {"id": "t2", "description": "summarize findings",
                 "assignee": "reviewer", "depends_on": ["t1"]},
            ]
            self.subtask_results = [
                {"subtask_id": "t1", "assignee": "researcher", "success": True,
                 "output": "refresh_token in auth.py refreshes session.token", "error": None,
                 "iterations": 3},
                {"subtask_id": "t2", "assignee": "reviewer", "success": True,
                 "output": "the change looks correct", "error": None, "iterations": 1},
            ]

    rec = record_from_outcome(
        goal, _Outcome(),
        planner_label="anthropic:claude-sonnet-4-6",
        roster={
            "researcher": "anthropic:claude-sonnet-4-6",
            "implementer": "cerebras:gpt-oss-120b",
            "reviewer": "gemini:gemini-2.0-flash",
            "tester": "groq:llama-3.3-70b",
        },
        faithfulness={"t1": {"score": 0.9, "grounded": True, "abstain": False}},
    )
    rec["id"] = run_id
    rec["faithfulness"] = {"score": 0.82, "grounded": True, "abstain": False}
    save_run(rec)
    return rec


def test_runs_empty_state(client: TestClient) -> None:
    """No data yet -> honest empty list (not a canned sample run)."""
    r = client.get("/ui/runs")
    assert r.status_code == 200
    assert r.json() == []


def test_runs_lists_real_orchestration(client: TestClient) -> None:
    _write_run("fix the auth refresh bug", run_id="run-42")
    r = client.get("/ui/runs")
    assert r.status_code == 200
    runs = r.json()
    assert len(runs) == 1
    run = runs[0]
    assert run["id"] == "run-42"
    assert run["kind"] == "orchestrate"
    assert run["title"] == "fix the auth refresh bug"
    assert run["subtasks"] == 2
    assert run["success"] is True


def test_run_detail_has_subagent_tree_and_faithfulness(client: TestClient) -> None:
    _write_run("fix the auth refresh bug", run_id="run-7")
    r = client.get("/ui/runs/run-7")
    assert r.status_code == 200
    d = r.json()

    # planner label is real
    assert d["planner"] == "anthropic:claude-sonnet-4-6"
    # the synthesized answer's faithfulness badge data is present and real
    assert d["faithfulness"]["grounded"] is True
    assert d["faithfulness"]["score"] == 0.82

    # sub-agent tree: every roster role is a branch, each with its provider.
    roles = {sa["role"]: sa for sa in d["subagents"]}
    assert {"researcher", "implementer", "reviewer", "tester"} <= set(roles)
    assert roles["implementer"]["provider"] == "cerebras:gpt-oss-120b"
    # the researcher branch carries the subtask the plan assigned to it...
    researcher_subtasks = roles["researcher"]["subtasks"]
    assert [s["id"] for s in researcher_subtasks] == ["t1"]
    # ...with its per-subtask faithfulness preserved.
    assert researcher_subtasks[0]["faithfulness"]["grounded"] is True
    # a role with no assigned subtask is still a branch but marked unused.
    assert roles["tester"]["used"] is False
    assert roles["tester"]["subtasks"] == []


def test_run_detail_404_for_unknown(client: TestClient) -> None:
    assert client.get("/ui/runs/nope").status_code == 404


def test_runs_includes_sessions(client: TestClient) -> None:
    """Chat sessions written by the sessions store also show up as runs."""
    from ronin_cli import sessions

    sessions._session_id = None  # fresh id for this process
    transcript = ["USER: how does login work?", "ASSISTANT: it uses oauth."]
    saved = sessions.save_session(Path.cwd(), transcript)
    assert saved is not None

    runs = client.get("/ui/runs").json()
    kinds = {r["kind"] for r in runs}
    assert "session" in kinds
    sess = next(r for r in runs if r["kind"] == "session")
    assert "login" in sess["title"]

    detail = client.get(f"/ui/runs/{sess['id']}").json()
    assert detail["kind"] == "session"
    assert detail["transcript"] == transcript


# ---------------------------------------------------------------------------
# Real data: memory
# ---------------------------------------------------------------------------

def test_memory_empty_state(client: TestClient) -> None:
    assert client.get("/ui/memory").json() == []


def test_memory_returns_real_entries(client: TestClient) -> None:
    from ronin_cli.memory_store import add_memory

    assert add_memory("User's name is Rohith")
    assert add_memory("Prefers Python and pytest")

    items = client.get("/ui/memory").json()
    texts = [m["text"] for m in items]
    assert "User's name is Rohith" in texts
    assert "Prefers Python and pytest" in texts


# ---------------------------------------------------------------------------
# Real data: skills
# ---------------------------------------------------------------------------

def test_skills_empty_state(client: TestClient) -> None:
    assert client.get("/ui/skills").json() == []


def test_skills_returns_real_crystallized_skills(client: TestClient) -> None:
    from ronin_cli.muscle_memory import crystallize

    crystallize(Path.cwd(), "deploy-staging", "Steps to deploy to staging:\n1. build\n2. push")

    skills = client.get("/ui/skills").json()
    assert len(skills) == 1
    assert skills[0]["name"] == "deploy-staging"
    assert "deploy to staging" in skills[0]["body"].lower()
