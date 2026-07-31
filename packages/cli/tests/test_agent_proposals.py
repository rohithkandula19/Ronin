"""Regression coverage for retained isolated-worktree agent patches."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ronin_cli.agent_proposals import AgentProposalStore, git_head
from ronin_cli.agent_state import AgentTaskStateStore
from ronin_cli.main import app
from ronin_cli.orchestrate import OrchestrateOutcome, _finish_outcome


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True,
    )
    return result.stdout


def _repo_with_patch(tmp_path: Path, *, ignore_runtime: bool = True) -> tuple[Path, str, str]:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.email", "tests@example.test")
    _git(root, "config", "user.name", "Ronin Tests")
    if ignore_runtime:
        (root / ".gitignore").write_text(".ronin/\n", encoding="utf-8")
    target = root / "sample.txt"
    target.write_text("before\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "seed")
    base = git_head(root)
    assert base is not None
    target.write_text("after\n", encoding="utf-8")
    patch = _git(root, "diff")
    _git(root, "checkout", "--", "sample.txt")
    return root, base, patch


def test_verified_proposal_stages_only_after_explicit_apply(tmp_path: Path) -> None:
    root, base, patch = _repo_with_patch(tmp_path)
    store = AgentProposalStore(root)
    proposal = store.record(
        run_id="agent-test-1", base_revision=base, diffs={"implementer": patch},
        success=True, subtask_results=[{"success": True}],
    )

    assert proposal.status == "ready"
    assert store.read_patch(proposal.run_id, "implementer") == patch
    application = store.apply(proposal.run_id)

    assert application.roles == ("implementer",)
    assert "+after" in _git(root, "diff", "--cached", "--", "sample.txt")
    assert _git(root, "log", "-1", "--format=%s").strip() == "seed"


def test_proposal_refuses_failed_stale_and_tampered_inputs(tmp_path: Path) -> None:
    root, base, patch = _repo_with_patch(tmp_path)
    store = AgentProposalStore(root)
    blocked = store.record(
        run_id="agent-failed", base_revision=base, diffs={"implementer": patch},
        success=False, subtask_results=[{"success": False}],
    )
    with pytest.raises(ValueError, match="not fully verified"):
        store.apply(blocked.run_id)

    ready = store.record(
        run_id="agent-ready", base_revision=base, diffs={"implementer": patch},
        success=True, subtask_results=[{"success": True}],
    )
    patch_path = root / ".ronin" / "agent-proposals" / ready.run_id / "implementer.patch"
    patch_path.write_text("not a patch\n", encoding="utf-8")
    with pytest.raises(ValueError, match="changed after recording"):
        store.apply(ready.run_id)

    replacement = store.record(
        run_id="agent-stale", base_revision=base, diffs={"implementer": patch},
        success=True, subtask_results=[{"success": True}],
    )
    (root / "other.txt").write_text("different\n", encoding="utf-8")
    _git(root, "add", "other.txt")
    _git(root, "commit", "-m", "move head")
    with pytest.raises(ValueError, match="stale"):
        store.apply(replacement.run_id)


def test_proposal_refuses_a_dirty_checkout(tmp_path: Path) -> None:
    root, base, patch = _repo_with_patch(tmp_path)
    proposal = AgentProposalStore(root).record(
        run_id="agent-dirty", base_revision=base, diffs={"implementer": patch},
        success=True, subtask_results=[{"success": True}],
    )
    (root / "unrelated.txt").write_text("local work\n", encoding="utf-8")

    with pytest.raises(ValueError, match="clean"):
        AgentProposalStore(root).apply(proposal.run_id)


def test_proposal_ignores_only_its_generated_runtime_records(tmp_path: Path) -> None:
    root, base, patch = _repo_with_patch(tmp_path, ignore_runtime=False)
    store = AgentProposalStore(root)
    proposal = store.record(
        run_id="agent-runtime", base_revision=base, diffs={"implementer": patch},
        success=True, subtask_results=[{"success": True}],
    )
    (root / ".ronin" / "agent-runs").mkdir(parents=True)
    (root / ".ronin" / "agent-runs" / "local.json").write_text("{}", encoding="utf-8")

    store.apply(proposal.run_id)
    assert "+after" in _git(root, "diff", "--cached", "--", "sample.txt")


def test_finish_outcome_retains_a_role_proposal_and_cli_applies_explicitly(tmp_path: Path) -> None:
    root, base, patch = _repo_with_patch(tmp_path)
    state_store = AgentTaskStateStore(root)
    state = state_store.create("edit sample", selection={}, workflow={}, governance={})
    outcome = OrchestrateOutcome(
        success=True, output="done", worktree_diffs={"implementer": patch}, base_revision=base,
        subtask_results=[{"success": True}],
    )

    finished = _finish_outcome(outcome, state_store, state, root=root, goal="edit sample")

    assert finished.proposal is not None
    assert AgentProposalStore(root).load(state["id"]).status == "ready"
    runner = CliRunner()
    listed = runner.invoke(app, ["util", "proposals", "list", "--root", str(root)])
    assert listed.exit_code == 0
    assert state["id"] in listed.stdout
    shown = runner.invoke(app, ["util", "proposals", "show", state["id"], "--role", "implementer", "--root", str(root)])
    assert shown.exit_code == 0
    assert "+after" in shown.stdout
    declined = runner.invoke(app, ["util", "proposals", "apply", state["id"], "--root", str(root)])
    assert declined.exit_code == 2
    assert "not staged" in declined.stdout
    applied = runner.invoke(app, ["util", "proposals", "apply", state["id"], "--root", str(root), "--yes"])
    assert applied.exit_code == 0, applied.stdout
    assert "staged" in applied.stdout
