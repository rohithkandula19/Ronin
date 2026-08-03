"""End-to-end evidence-gate coverage for the durable Issue-to-PR workflow."""
from __future__ import annotations

import subprocess
from pathlib import Path

from typer.testing import CliRunner

from ronin_cli.candidate_workspace import CandidateCommandResult, CandidateWorkspaceService
from ronin_cli.main import app
from ronin_cli.mission_models import MissionBudget, MissionSpec, MissionStage, PlanArtifact, PlanStep
from ronin_cli.mission_store import MissionStore
from ronin_cli.mission_workflow import MissionWorkflow


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True)


def _repo(root: Path) -> None:
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "workflow@example.invalid")
    _git(root, "config", "user.name", "Mission workflow tests")
    (root / "app.py").write_text("VALUE = 'base'\n", encoding="utf-8")
    _git(root, "add", "app.py")
    _git(root, "commit", "-qm", "initial")


def _mission(root: Path, *, repairs: int = 3):
    return MissionStore(root).create(
        MissionSpec(title="Add verified retry handling", issue_text="Add a bounded retry path."),
        budget=MissionBudget(max_repair_attempts=repairs),
    )


def _passed_runner(argv, **kwargs):
    return subprocess.CompletedProcess(argv, 0, stdout="1 passed\n", stderr="")


def _failed_runner(argv, **kwargs):
    return subprocess.CompletedProcess(argv, 1, stdout="1 failed\n", stderr="")


def _prepare_candidate(root: Path, mission_id: str):
    candidate = CandidateWorkspaceService(root).create(mission_id, image="python:3.14-alpine")
    MissionStore(root).set_candidate_workspace(mission_id, candidate.id)
    (Path(candidate.path) / "retry.py").write_text("def retry():\n    return True\n", encoding="utf-8")
    return candidate


def _plan() -> PlanArtifact:
    return PlanArtifact(
        steps=[PlanStep(id="retry", description="implement the bounded retry path", files=["retry.py"])],
        files_to_change=["retry.py"],
        test_additions=["test_retry.py"],
        rollback_strategy="Remove the retry branch.",
    )


def test_issue_to_pr_workflow_requires_real_evidence_at_every_gate(tmp_path: Path) -> None:
    _repo(tmp_path)
    mission = _mission(tmp_path)
    workflow = MissionWorkflow(tmp_path)

    implementing = workflow.record_plan(mission.id, _plan())
    assert implementing.stage is MissionStage.IMPLEMENTING
    candidate = _prepare_candidate(tmp_path, mission.id)

    reviewing = workflow.verify_candidate(mission.id, "pytest -q", run_fn=_passed_runner)
    assert reviewing.stage is MissionStage.REVIEWING
    assert reviewing.artifacts.test_report is not None
    assert reviewing.artifacts.test_report.verdict == "passed"
    assert reviewing.usage.tool_calls == 1

    security = workflow.review_candidate(mission.id)
    assert security.stage is MissionStage.SECURITY
    assert security.artifacts.review_report is not None
    assert security.artifacts.review_report.verdict == "approve"

    approval = workflow.scan_candidate_security(mission.id)
    assert approval.stage is MissionStage.AWAITING_APPROVAL
    assert approval.artifacts.security_scan is not None
    assert approval.artifacts.security_scan.verdict == "passed"

    evaluated = workflow.evaluate(mission.id)
    assert evaluated.artifacts.evaluation_gate is not None
    assert evaluated.artifacts.evaluation_gate.eligible
    staged = workflow.prepare_pull_request_draft(mission.id, approved_by="Rohith")
    assert staged.stage is MissionStage.STAGING
    assert staged.artifacts.pull_request_draft is not None
    assert staged.artifacts.pull_request_draft.status == "ready"
    assert staged.artifacts.pull_request_draft.suggested_branch == f"ronin/{mission.id}"
    assert MissionStore(tmp_path).verify_audit(mission.id).valid
    CandidateWorkspaceService(tmp_path).destroy(candidate.id)


def test_failed_candidate_tests_use_only_the_configured_repair_budget(tmp_path: Path) -> None:
    _repo(tmp_path)
    mission = _mission(tmp_path, repairs=1)
    workflow = MissionWorkflow(tmp_path)
    workflow.record_plan(mission.id, _plan())
    candidate = _prepare_candidate(tmp_path, mission.id)

    repair = workflow.verify_candidate(mission.id, "pytest -q", run_fn=_failed_runner)
    assert repair.stage is MissionStage.IMPLEMENTING
    assert repair.usage.repair_attempts == 1
    exhausted = workflow.verify_candidate(mission.id, "pytest -q", run_fn=_failed_runner)
    assert exhausted.stage is MissionStage.FAILED
    assert exhausted.usage.repair_attempts == 2
    assert "retry budget" in (exhausted.error or "")
    CandidateWorkspaceService(tmp_path).destroy(candidate.id)


def test_security_gate_returns_candidate_to_implementation_without_leaking_secret_text(tmp_path: Path) -> None:
    _repo(tmp_path)
    mission = _mission(tmp_path)
    workflow = MissionWorkflow(tmp_path)
    workflow.record_plan(mission.id, _plan())
    candidate = _prepare_candidate(tmp_path, mission.id)
    workflow.verify_candidate(mission.id, "pytest -q", run_fn=_passed_runner)
    workflow.review_candidate(mission.id)
    secret = "gh" + "p_" + "A" * 36
    (Path(candidate.path) / "secret.py").write_text(f"TOKEN = '{secret}'\n", encoding="utf-8")

    remediation = workflow.scan_candidate_security(mission.id)
    scan = remediation.artifacts.security_scan
    assert remediation.stage is MissionStage.IMPLEMENTING
    assert scan is not None and scan.verdict == "failed"
    assert scan.findings[0].severity == "blocking"
    assert secret not in scan.findings[0].detail
    CandidateWorkspaceService(tmp_path).destroy(candidate.id)


def test_mission_cli_runs_the_evidence_gates_before_writing_a_local_pr_draft(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _repo(tmp_path)
    mission = _mission(tmp_path)
    runner = CliRunner()

    planned = runner.invoke(
        app,
        [
            "util", "mission", "plan", mission.id, "--root", str(tmp_path),
            "--step", "Implement retry", "--file", "retry.py", "--test-addition", "test_retry.py",
        ],
    )
    assert planned.exit_code == 0, planned.stdout
    candidate = _prepare_candidate(tmp_path, mission.id)

    def fake_run(self, candidate_id, command, **kwargs):
        return CandidateCommandResult(command=command, exit_code=0, duration_seconds=0.1)

    monkeypatch.setattr(CandidateWorkspaceService, "run_command", fake_run)
    verified = runner.invoke(
        app, ["util", "mission", "verify", mission.id, "pytest -q", "--root", str(tmp_path), "--yes"],
    )
    assert verified.exit_code == 0, verified.stdout
    reviewed = runner.invoke(app, ["util", "mission", "review", mission.id, "--root", str(tmp_path)])
    assert reviewed.exit_code == 0, reviewed.stdout
    secured = runner.invoke(app, ["util", "mission", "security", mission.id, "--root", str(tmp_path)])
    assert secured.exit_code == 0, secured.stdout
    evaluation = runner.invoke(app, ["util", "mission", "evaluate", mission.id, "--root", str(tmp_path)])
    assert evaluation.exit_code == 0, evaluation.stdout
    drafted = runner.invoke(
        app,
        ["util", "mission", "draft-pr", mission.id, "--root", str(tmp_path), "--approved-by", "Rohith", "--yes"],
    )
    assert drafted.exit_code == 0, drafted.stdout
    assert MissionStore(tmp_path).load(mission.id).artifacts.pull_request_draft is not None
    CandidateWorkspaceService(tmp_path).destroy(candidate.id)
