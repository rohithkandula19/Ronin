"""Offline contracts for leased remote candidate verification workers."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from ronin_cli.candidate_workspace import CandidateWorkspaceService
from ronin_cli.main import app
from ronin_cli.mission_models import MissionSpec, MissionStage, PlanArtifact, PlanStep
from ronin_cli.mission_store import MissionStore
from ronin_cli.mission_workflow import MissionWorkflow
from ronin_cli.remote_workers import (
    RemoteVerificationCoordinator,
    RemoteWorkerAuth,
    RemoteWorkerDispatch,
    RemoteWorkerEvidence,
    RemoteWorkerStore,
    execute_remote_dispatch,
    remote_docker_argv,
)


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True)


def _prepared_mission(root: Path) -> tuple[str, str]:
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "remote-worker@example.invalid")
    _git(root, "config", "user.name", "Remote worker tests")
    _git(root, "remote", "add", "origin", "https://github.com/example/ronin.git")
    (root / "app.py").write_text("VALUE = 'base'\n", encoding="utf-8")
    _git(root, "add", "app.py")
    _git(root, "commit", "-qm", "initial")
    mission = MissionStore(root).create(
        MissionSpec(title="Verify remotely", issue_text="Run a candidate in a remote Docker worker."),
    )
    MissionWorkflow(root).record_plan(
        mission.id,
        PlanArtifact(steps=[PlanStep(id="change", description="change app", files=["app.py"])]),
    )
    candidate = CandidateWorkspaceService(root).create(mission.id, image="python:3.14-alpine")
    MissionStore(root).set_candidate_workspace(mission.id, candidate.id)
    (Path(candidate.path) / "app.py").write_text("VALUE = 'candidate'\n", encoding="utf-8")
    return mission.id, candidate.id


def test_remote_worker_lease_advances_only_a_current_candidate(tmp_path: Path) -> None:
    mission_id, candidate_id = _prepared_mission(tmp_path)
    coordinator = RemoteVerificationCoordinator(tmp_path)
    job = coordinator.enqueue(mission_id, "pytest -q", repository_url="https://github.com/example/ronin.git")
    from csk_api.dashboard import remote_worker_job_entries

    dashboard_job = remote_worker_job_entries(tmp_path)[0]
    assert dashboard_job["id"] == job.id
    assert "patch" not in dashboard_job and "command" not in dashboard_job and "repository_url" not in dashboard_job
    dispatch = RemoteWorkerStore(tmp_path).claim_next("builder-01")

    assert dispatch is not None
    assert dispatch.job_id == job.id
    assert dispatch.candidate_id == candidate_id
    assert "candidate" in dispatch.patch
    completed = coordinator.complete(
        job.id,
        worker_id="builder-01",
        lease_id=dispatch.lease_id,
        lease_token=dispatch.lease_token,
        evidence=RemoteWorkerEvidence("passed", exit_code=0, duration_seconds=1.5, summary="Docker tests passed"),
    )

    assert completed.status == "completed"
    mission = MissionStore(tmp_path).load(mission_id)
    assert mission.stage is MissionStage.REVIEWING
    assert mission.artifacts.test_report is not None
    assert mission.artifacts.test_report.verdict == "passed"
    assert MissionStore(tmp_path).events(mission_id)[-1].actor == "remote-worker:builder-01"
    assert MissionStore(tmp_path).verify_audit(mission_id).valid
    CandidateWorkspaceService(tmp_path).destroy(candidate_id)


def test_remote_worker_completion_is_blocked_when_candidate_patch_changes(tmp_path: Path) -> None:
    mission_id, candidate_id = _prepared_mission(tmp_path)
    coordinator = RemoteVerificationCoordinator(tmp_path)
    job = coordinator.enqueue(mission_id, "pytest -q", repository_url="https://github.com/example/ronin.git")
    dispatch = RemoteWorkerStore(tmp_path).claim_next("builder-02")
    assert dispatch is not None
    (Path(CandidateWorkspaceService(tmp_path).load(candidate_id).path) / "app.py").write_text(
        "VALUE = 'changed-after-dispatch'\n", encoding="utf-8",
    )

    completed = coordinator.complete(
        job.id,
        worker_id="builder-02",
        lease_id=dispatch.lease_id,
        lease_token=dispatch.lease_token,
        evidence=RemoteWorkerEvidence("passed", exit_code=0, duration_seconds=1.0),
    )

    assert completed.status == "failed"
    assert completed.evidence is not None and completed.evidence.outcome == "blocked"
    assert MissionStore(tmp_path).load(mission_id).stage is MissionStage.IMPLEMENTING
    CandidateWorkspaceService(tmp_path).destroy(candidate_id)


def test_remote_worker_cli_enqueues_a_snapshot_without_starting_a_worker(tmp_path: Path) -> None:
    mission_id, candidate_id = _prepared_mission(tmp_path)

    result = CliRunner().invoke(
        app,
        [
            "util", "mission", "worker", "enqueue", mission_id, "pytest -q", "--root", str(tmp_path),
            "--repository-url", "https://github.com/example/ronin.git",
        ],
    )

    assert result.exit_code == 0, result.stdout
    jobs = RemoteWorkerStore(tmp_path).list()
    assert len(jobs) == 1 and jobs[0].candidate_id == candidate_id and jobs[0].status == "queued"
    CandidateWorkspaceService(tmp_path).destroy(candidate_id)


def test_worker_auth_is_salted_and_never_persists_the_plain_token(tmp_path: Path) -> None:
    auth = RemoteWorkerAuth(tmp_path)
    token = auth.provision()
    raw = json.loads((tmp_path / ".ronin" / "remote-worker-auth.json").read_text(encoding="utf-8"))

    assert auth.verify(token)
    assert not auth.verify("wrong-token")
    assert token not in json.dumps(raw)
    with pytest.raises(ValueError, match="already exists"):
        auth.provision()


def test_remote_worker_evidence_rejects_inconsistent_outcome_and_exit_code() -> None:
    with pytest.raises(ValueError, match="exit code 0"):
        RemoteWorkerEvidence("passed", exit_code=1)
    with pytest.raises(ValueError, match="nonzero"):
        RemoteWorkerEvidence("failed", exit_code=0)


def test_remote_dispatch_runner_uses_no_network_docker_and_never_returns_logs(tmp_path: Path) -> None:
    dispatch = RemoteWorkerDispatch(
        job_id="remote-job-20260803-120000-abcdef",
        mission_id="mission-20260803-120000-abcdef",
        candidate_id="candidate-20260803-120000-abcdef",
        repository_url="https://github.com/example/ronin.git",
        base_revision="a" * 40,
        patch="diff --git a/app.py b/app.py\nindex 1111111..2222222 100644\n--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+new\n",
        patch_digest="",
        image="python:3.14-alpine",
        command="pytest -q",
        timeout_seconds=30,
        worker_id="builder-03",
        lease_id="b" * 32,
        lease_token="lease-token",
        lease_expires="2026-08-03T12:10:00+00:00",
    )
    dispatch = dispatch.__class__(**{**dispatch.__dict__, "patch_digest": hashlib.sha256(dispatch.patch.encode()).hexdigest()})
    calls: list[list[str]] = []

    def runner(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="private output", stderr="private error")

    result = execute_remote_dispatch(dispatch, runner=runner)

    assert result.outcome == "passed"
    assert result.summary == "remote Docker command completed"
    docker = calls[-1]
    assert docker[0:4] == ["docker", "run", "--rm", "--network"]
    assert "none" in docker and ["--cap-drop", "ALL"] == docker[docker.index("--cap-drop"):docker.index("--cap-drop") + 2]
    assert remote_docker_argv(tmp_path, "python:3.14-alpine", "pytest -q")[-1] == "pytest -q"


def test_remote_worker_api_requires_controller_token_and_records_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mission_id, candidate_id = _prepared_mission(tmp_path)
    RemoteVerificationCoordinator(tmp_path).enqueue(
        mission_id, "pytest -q", repository_url="https://github.com/example/ronin.git",
    )
    token = RemoteWorkerAuth(tmp_path).provision()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'api.db'}")
    from csk_api.db import reset_db_for_tests
    from csk_api.main import make_app

    reset_db_for_tests(f"sqlite:///{tmp_path / 'api.db'}")
    client = TestClient(make_app())
    assert client.post("/worker/v1/claim", json={"worker_id": "api-worker"}).status_code == 401
    claimed = client.post(
        "/worker/v1/claim", json={"worker_id": "api-worker"}, headers={"X-Ronin-Worker-Token": token},
    )
    assert claimed.status_code == 200
    dispatch = claimed.json()["dispatch"]
    assert "patch" in dispatch and "lease_token" in dispatch
    completed = client.post(
        "/worker/v1/complete",
        json={
            "job_id": dispatch["job_id"], "worker_id": "api-worker", "lease_id": dispatch["lease_id"],
            "lease_token": dispatch["lease_token"],
            "evidence": {"outcome": "passed", "exit_code": 0, "duration_seconds": 0.1, "summary": "passed"},
        },
        headers={"X-Ronin-Worker-Token": token},
    )
    assert completed.status_code == 200
    assert completed.json()["job"]["status"] == "completed"
    assert MissionStore(tmp_path).load(mission_id).stage is MissionStage.REVIEWING
    CandidateWorkspaceService(tmp_path).destroy(candidate_id)
