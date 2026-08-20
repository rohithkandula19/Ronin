"""Persistence and tamper-evidence coverage for durable missions."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ronin_cli.candidate_workspace import CandidateWorkspaceService
from ronin_cli.main import app
from ronin_cli.mission_models import MissionArtifacts, MissionSpec, MissionStage, PlanArtifact, PlanStep
from ronin_cli.mission_store import MissionStore


def _spec() -> MissionSpec:
    return MissionSpec(
        source="github",
        source_id="123",
        title="Add verified retries",
        issue_text="Retry a transient request exactly three times.",
        acceptance_criteria=["A transient request is retried."],
    )


def test_mission_store_persists_artifacts_transitions_and_a_hash_chain(tmp_path) -> None:
    store = MissionStore(tmp_path)
    created = store.create(_spec())
    inspecting = store.transition(created.id, MissionStage.INSPECTING, summary="issue parsed")
    planning = store.transition(inspecting.id, MissionStage.PLANNING, actor="architect")
    saved = store.save_artifacts(
        planning.id,
        MissionArtifacts(plan=PlanArtifact(steps=[PlanStep(id="retry", description="add a retry loop")])),
    )

    assert saved.stage is MissionStage.PLANNING
    assert saved.artifacts.plan is not None
    assert saved.artifacts.plan.steps[0].id == "retry"
    assert [event.kind for event in store.events(saved.id)] == [
        "mission_created",
        "stage_transition",
        "stage_transition",
        "artifacts_recorded",
    ]
    assert store.verify_audit(saved.id).valid
    assert store.verify_audit(saved.id).events == 4
    assert store.load(saved.id).last_event_hash == store.events(saved.id)[-1].event_hash


def test_mission_store_detects_tampered_audit_event_and_path_escape(tmp_path) -> None:
    store = MissionStore(tmp_path)
    mission = store.create(_spec())
    event_path = tmp_path / ".ronin" / "missions" / f"{mission.id}.events.jsonl"
    event = json.loads(event_path.read_text(encoding="utf-8"))
    event["summary"] = "mutated after write"
    event_path.write_text(json.dumps(event) + "\n", encoding="utf-8")

    report = store.verify_audit(mission.id)
    assert not report.valid
    assert "hash" in (report.error or "")
    with pytest.raises(ValueError, match="invalid mission id"):
        store.load("../" + mission.id)


def test_mission_cli_creates_audits_and_manages_a_candidate_checkout(tmp_path: Path) -> None:
    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True, text=True)

    git("init", "-q")
    git("config", "user.email", "mission-cli@example.invalid")
    git("config", "user.name", "Mission CLI tests")
    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    git("add", "app.py")
    git("commit", "-qm", "initial")
    runner = CliRunner()

    created = runner.invoke(
        app,
        [
            "util", "mission", "create", "Verify retry behavior", "Add resilient retry coverage.",
            "--root", str(tmp_path), "--source", "github", "--source-id", "42",
            "--acceptance", "Retries have a regression test.",
        ],
    )
    assert created.exit_code == 0, created.stdout
    mission = MissionStore(tmp_path).list()[0]
    assert mission.spec.source == "github"

    advanced = runner.invoke(app, ["util", "mission", "advance", mission.id, "inspecting", "--root", str(tmp_path)])
    assert advanced.exit_code == 0, advanced.stdout
    workspace = runner.invoke(
        app,
        ["util", "mission", "workspace", "create", mission.id, "--root", str(tmp_path), "--image", "python:3.14-alpine"],
    )
    assert workspace.exit_code == 0, workspace.stdout
    candidate = CandidateWorkspaceService(tmp_path).list()[0]
    assert MissionStore(tmp_path).load(mission.id).candidate_workspace_id == candidate.id

    refused = runner.invoke(app, ["util", "mission", "workspace", "destroy", candidate.id, "--root", str(tmp_path)])
    assert refused.exit_code == 2
    assert Path(candidate.path).is_dir()
    destroyed = runner.invoke(
        app, ["util", "mission", "workspace", "destroy", candidate.id, "--root", str(tmp_path), "--yes"],
    )
    assert destroyed.exit_code == 0, destroyed.stdout
    audited = runner.invoke(app, ["util", "mission", "audit", mission.id, "--root", str(tmp_path)])
    assert audited.exit_code == 0, audited.stdout
