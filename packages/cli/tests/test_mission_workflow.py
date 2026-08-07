"""End-to-end evidence-gate coverage for the durable Issue-to-PR workflow."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ronin_cli.candidate_workspace import CandidateCommandResult, CandidateWorkspaceService
from ronin_cli.main import app
from ronin_cli.mission_models import (
    IssueAnalysis,
    MissionBudget,
    MissionSpec,
    MissionStage,
    PlanArtifact,
    PlanStep,
    RepositoryMap,
    RootCauseAnalysis,
    SelfReviewNotes,
)
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


def _verified_mission(root: Path):
    return MissionStore(root).create(
        MissionSpec(
            title="Fix retry failure", issue_text="Retries stop too early.", workflow="verified",
            acceptance_criteria=["Retry transient failures within the configured limit."],
        ),
    )


def _record_verified_discovery(workflow: MissionWorkflow, mission_id: str) -> None:
    workflow.record_issue_analysis(mission_id, IssueAnalysis(
        summary="Retry handling stops after the first transient failure.",
        symptoms=["Requests fail before the configured retry limit."],
        expected_behavior=["Transient failures are retried within the configured limit."],
        reproduction_steps=["Run the retry regression test with a transient failure."],
        acceptance_criteria=["Transient failures are retried within the configured limit."],
        involved_files=["retry.py", "tests/test_retry.py"],
    ))
    workflow.record_repository_map(mission_id, RepositoryMap(
        source_directories=["."], test_directories=["tests"], configuration_files=["pyproject.toml"],
        test_commands=["pytest -q"], architectural_patterns=["bounded retry helper"],
    ))
    workflow.record_root_cause(mission_id, RootCauseAnalysis(
        broken_behavior="The retry helper exits after one transient failure.",
        cause="The retry branch does not loop through remaining attempts.",
        responsible_logic="retry.py retry branch returns before the next attempt.",
        behavior_gap="Configured retry limits are never reached.",
        evidence_references=["retry.py", "tests/test_retry.py"], affected_files=["retry.py"],
    ))


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


def test_mission_agent_implementation_stays_in_candidate_and_records_evidence(tmp_path: Path) -> None:
    _repo(tmp_path)
    mission = _mission(tmp_path)
    workflow = MissionWorkflow(tmp_path)
    workflow.record_plan(mission.id, _plan())
    candidate = _prepare_candidate(tmp_path, mission.id)

    class Result:
        success = True
        iterations = 3
        usage = {"input_tokens": 12, "output_tokens": 8}
        error = None
        steps = []

    def runner(task: str, candidate_root: Path, max_iterations: int):
        assert "approved mission" in task
        assert max_iterations == 7
        (candidate_root / "agent_change.py").write_text("VALUE = 'candidate'\n", encoding="utf-8")
        return Result()

    recorded = workflow.execute_implementation(mission.id, runner, max_iterations=7)

    evidence = recorded.artifacts.implementation_evidence[-1]
    assert recorded.stage is MissionStage.IMPLEMENTING
    assert evidence.success is True
    assert evidence.input_tokens == 12 and evidence.output_tokens == 8
    assert "agent_change.py" in evidence.changed_files
    assert (Path(candidate.path) / "agent_change.py").is_file()
    assert not (tmp_path / "agent_change.py").exists()
    assert recorded.usage.tokens == 20
    assert MissionStore(tmp_path).verify_audit(mission.id).valid
    CandidateWorkspaceService(tmp_path).destroy(candidate.id)


def test_mission_implement_cli_forces_candidate_sandbox(tmp_path: Path, monkeypatch) -> None:
    _repo(tmp_path)
    mission = _mission(tmp_path)
    MissionWorkflow(tmp_path).record_plan(mission.id, _plan())
    candidate = _prepare_candidate(tmp_path, mission.id)
    seen: dict[str, object] = {}

    class Result:
        success = True
        iterations = 1
        usage = {}
        error = None
        steps = []

    def fake_run(config, task, **kwargs):
        seen.update(config=config, task=task, **kwargs)
        return Result()

    monkeypatch.setattr("ronin_cli.code_mode.run_code_agent", fake_run)
    result = CliRunner().invoke(
        app,
        ["util", "mission", "implement", mission.id, "--root", str(tmp_path), "--offline"],
    )

    assert result.exit_code == 0, result.stdout
    assert seen["root"] == Path(candidate.path)
    assert seen["yolo"] is True
    assert seen["config"].full_access is False
    CandidateWorkspaceService(tmp_path).destroy(candidate.id)


def test_verified_issue_to_pr_workflow_requires_discovery_plan_approval_and_self_review(tmp_path: Path) -> None:
    _repo(tmp_path)
    mission = _verified_mission(tmp_path)
    workflow = MissionWorkflow(tmp_path)

    with pytest.raises(ValueError, match="issue analysis"):
        workflow.record_plan(mission.id, _plan())
    _record_verified_discovery(workflow, mission.id)

    planned = workflow.record_plan(mission.id, _plan())
    assert planned.stage is MissionStage.PLANNING
    assert planned.artifacts.plan is not None and not planned.artifacts.plan.approved_by
    implementing = workflow.approve_plan(mission.id, approved_by="Rohith")
    assert implementing.stage is MissionStage.IMPLEMENTING
    assert implementing.artifacts.plan is not None and implementing.artifacts.plan.approved_by == "Rohith"

    candidate = _prepare_candidate(tmp_path, mission.id)
    assert workflow.verify_candidate(mission.id, "pytest -q", run_fn=_passed_runner).stage is MissionStage.REVIEWING
    assert workflow.review_candidate(mission.id).stage is MissionStage.SECURITY
    awaiting = workflow.scan_candidate_security(mission.id)
    assert awaiting.stage is MissionStage.AWAITING_APPROVAL
    assert awaiting.artifacts.verification_report is not None
    assert awaiting.artifacts.verification_report.verdict == "passed"

    workflow.record_self_review(mission.id, SelfReviewNotes(
        reviewer="Rohith", checked=["scope", "error handling", "edge cases"],
        notes="No additional issues found.",
    ))
    evaluated = workflow.evaluate(mission.id)
    assert evaluated.artifacts.evaluation_gate is not None and evaluated.artifacts.evaluation_gate.eligible
    staged = workflow.prepare_pull_request_draft(mission.id, approved_by="Rohith")
    draft = staged.artifacts.pull_request_draft
    assert staged.stage is MissionStage.STAGING and draft is not None
    assert "## Root Cause" in draft.body
    assert "## Self-Review" in draft.body
    assert "Plan approved by: Rohith" in draft.body
    CandidateWorkspaceService(tmp_path).destroy(candidate.id)


def test_verified_mission_cli_records_discovery_before_named_plan_approval(tmp_path: Path) -> None:
    runner = CliRunner()
    created = runner.invoke(
        app,
        ["util", "mission", "create", "Fix retry failure", "Retries stop too early.", "--verified", "--root", str(tmp_path)],
    )
    assert created.exit_code == 0, created.stdout
    mission = MissionStore(tmp_path).list()[0]

    inspected = runner.invoke(
        app,
        ["util", "mission", "inspect", mission.id, "--summary", "Retries stop after one failure.",
         "--reproduce", "Run the retry regression test.", "--root", str(tmp_path)],
    )
    assert inspected.exit_code == 0, inspected.stdout
    mapped = runner.invoke(
        app,
        ["util", "mission", "map", mission.id, "--source-dir", "src", "--test-dir", "tests",
         "--test-command", "pytest -q", "--root", str(tmp_path)],
    )
    assert mapped.exit_code == 0, mapped.stdout
    rca = runner.invoke(
        app,
        ["util", "mission", "rca", mission.id, "--broken", "Retry exits early.",
         "--cause", "The loop returns too soon.", "--logic", "The retry branch returns before retrying.",
         "--gap", "The configured limit is not reached.", "--root", str(tmp_path)],
    )
    assert rca.exit_code == 0, rca.stdout
    planned = runner.invoke(
        app,
        ["util", "mission", "plan", mission.id, "--step", "Repair the retry loop", "--file", "retry.py",
         "--root", str(tmp_path)],
    )
    assert planned.exit_code == 0, planned.stdout
    assert MissionStore(tmp_path).load(mission.id).stage is MissionStage.PLANNING
    approved = runner.invoke(
        app,
        ["util", "mission", "approve-plan", mission.id, "--approved-by", "Rohith", "--yes", "--root", str(tmp_path)],
    )
    assert approved.exit_code == 0, approved.stdout
    assert MissionStore(tmp_path).load(mission.id).stage is MissionStage.IMPLEMENTING


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
