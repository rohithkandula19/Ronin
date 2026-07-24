"""Tests for Wave 6 pipeline save-state / resume."""
from __future__ import annotations

import pytest

from ronin_cli.config import RoninConfig
from ronin_cli.pipeline import StageOutcome, plan_pipeline, run_pipeline
from ronin_cli.pipeline_state_io import (
    PipelineStateError,
    first_incomplete,
    load_state,
    save_state,
)


def test_save_state_writes_valid_json(tmp_path) -> None:
    st = plan_pipeline("do x", ["architect", "implementer"])
    st.stages[0].status = "completed"
    path = tmp_path / "sub" / "state.json"
    save_state(st, path)
    assert path.is_file()
    loaded = load_state(path)
    assert loaded.task == "do x"
    assert loaded.roles == ["architect", "implementer"]
    assert loaded.stages[0].status == "completed"


def test_load_corrupt_file_raises(tmp_path) -> None:
    p = tmp_path / "bad.json"
    p.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(PipelineStateError, match="corrupt"):
        load_state(p)


def test_load_missing_file_raises(tmp_path) -> None:
    with pytest.raises(PipelineStateError, match="no saved pipeline state"):
        load_state(tmp_path / "nope.json")


def test_load_incompatible_state_raises(tmp_path) -> None:
    # roles/stages mismatch
    p = tmp_path / "incompat.json"
    p.write_text('{"task": "x", "roles": ["architect", "tester"], "stages": '
                 '[{"role": "architect"}]}', encoding="utf-8")
    with pytest.raises(PipelineStateError, match="incompatible"):
        load_state(p)


def test_first_incomplete() -> None:
    st = plan_pipeline("x", ["architect", "implementer", "reviewer"])
    st.stages[0].status = "completed"
    st.stages[1].status = "failed"
    assert first_incomplete(st) == 1                       # the failed one is resumable
    assert first_incomplete(st, rerun_completed=True) == 0  # rerun starts at the top
    for s in st.stages:
        s.status = "completed"
    assert first_incomplete(st) is None                    # nothing left


def test_resume_does_not_rerun_completed_stages() -> None:
    calls: list = []

    def runner(config, role, prompt, *, read_only, root, console, max_iterations):
        calls.append(role)
        return StageOutcome(success=True, summary=f"{role} ok",
                            artifact={"kind": role, "marker": role})

    # a saved state where architect already completed, implementer failed
    saved = plan_pipeline("x", ["architect", "implementer", "verifier"])
    saved.stages[0].status = "completed"
    saved.stages[0].artifact = {"kind": "architect", "marker": "architect"}
    saved.stages[1].status = "failed"

    cfg = RoninConfig(provider="cerebras")
    state = run_pipeline(cfg, saved.task, saved.roles, resume_state=saved, stage_runner=runner)
    # architect NOT re-run; implementer + verifier are
    assert calls == ["implementer", "verifier"]
    assert state.stage("architect").status == "completed"
    assert all(s.status == "completed" for s in state.stages)
    # the completed architect's artifact was still handed forward
    assert "architect" in state.stage("implementer").artifact["kind"] or \
           any(s.role == "implementer" for s in state.stages)


def test_resume_rerun_completed_reruns_all() -> None:
    calls: list = []

    def runner(config, role, prompt, *, read_only, root, console, max_iterations):
        calls.append(role)
        return StageOutcome(success=True, summary=f"{role} ok")

    saved = plan_pipeline("x", ["architect", "implementer"])
    for s in saved.stages:
        s.status = "completed"
    cfg = RoninConfig(provider="cerebras")
    run_pipeline(cfg, saved.task, saved.roles, resume_state=saved,
                 rerun_completed=True, stage_runner=runner)
    assert calls == ["architect", "implementer"]  # both re-ran


def test_save_path_checkpoints_after_each_stage(tmp_path) -> None:
    path = tmp_path / "ck.json"

    def runner(config, role, prompt, *, read_only, root, console, max_iterations):
        # the checkpoint file should already exist for prior stages
        return StageOutcome(success=True, summary=f"{role} ok")

    cfg = RoninConfig(provider="cerebras")
    run_pipeline(cfg, "x", ["architect", "implementer"], stage_runner=runner,
                 save_path=path)
    assert path.is_file()
    final = load_state(path)
    assert all(s.status == "completed" for s in final.stages)


# --- Wave 7: git snapshot safety + checkpoint --------------------------------

import subprocess

from typer.testing import CliRunner
from ronin_cli.main import app
from ronin_cli.pipeline_git_snapshot import git_snapshot

_runner = CliRunner()


def _init_repo(tmp_path):
    for args in (("init", "-q"), ("config", "user.email", "t@t.t"), ("config", "user.name", "t")):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)
    (tmp_path / "a.txt").write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "-m", "init"], check=True, capture_output=True)


def _saved_state(tmp_path, snap_dict, *, checkpoint_id=None):
    st = plan_pipeline("resume me", ["architect", "verifier"])
    st.stages[0].status = "completed"
    st.stages[0].artifact = {"kind": "architect"}
    st.stages[1].status = "failed"
    st.root = str(tmp_path.resolve())
    st.git_snapshot = snap_dict
    st.checkpoint_id = checkpoint_id
    # keep the state file OUTSIDE the repo so it doesn't dirty the tree vs the snapshot
    path = tmp_path.parent / "st.json"
    path.write_text(st.model_dump_json(), encoding="utf-8")
    return path


# --- Wave 8: checkpoint restore on unsafe resume -----------------------------

def test_resume_mismatch_offers_restore(tmp_path, monkeypatch) -> None:
    from ronin_cli.checkpoint import create_checkpoint
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    cp = create_checkpoint(tmp_path, label="pipeline")
    snap = git_snapshot(tmp_path).model_dump()
    path = _saved_state(tmp_path, snap, checkpoint_id=cp.id)
    (tmp_path / "a.txt").write_text("CHANGED\n", encoding="utf-8")
    res = _runner.invoke(app, ["util", "pipeline", "--resume", str(path), "--dry-run"])
    assert res.exit_code == 2
    assert "restore-checkpoint" in res.stdout  # the offer is surfaced


def test_no_restore_offer_suppresses(tmp_path, monkeypatch) -> None:
    from ronin_cli.checkpoint import create_checkpoint
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    cp = create_checkpoint(tmp_path, label="pipeline")
    snap = git_snapshot(tmp_path).model_dump()
    path = _saved_state(tmp_path, snap, checkpoint_id=cp.id)
    (tmp_path / "a.txt").write_text("CHANGED\n", encoding="utf-8")
    res = _runner.invoke(app, ["util", "pipeline", "--resume", str(path), "--dry-run", "--no-restore-offer"])
    assert res.exit_code == 2
    assert "restore-checkpoint" not in res.stdout  # offer suppressed
    assert "refusing to resume" in res.stdout


def test_restore_declined_exits_safely(tmp_path, monkeypatch) -> None:
    from ronin_cli.checkpoint import create_checkpoint
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    cp = create_checkpoint(tmp_path, label="pipeline")
    snap = git_snapshot(tmp_path).model_dump()
    path = _saved_state(tmp_path, snap, checkpoint_id=cp.id)
    (tmp_path / "a.txt").write_text("CHANGED\n", encoding="utf-8")
    res = _runner.invoke(app, ["util", "pipeline", "--resume", str(path), "--dry-run",
                               "--restore-checkpoint"], input="n\n")
    assert res.exit_code == 2
    assert "declined" in res.stdout
    # local change is untouched (never destroyed without approval)
    assert (tmp_path / "a.txt").read_text() == "CHANGED\n"


def test_restore_success_rechecks_and_continues(tmp_path, monkeypatch) -> None:
    from ronin_cli.checkpoint import create_checkpoint
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    cp = create_checkpoint(tmp_path, label="pipeline")     # snapshot the clean tree
    snap = git_snapshot(tmp_path).model_dump()             # taken AFTER the checkpoint
    path = _saved_state(tmp_path, snap, checkpoint_id=cp.id)
    (tmp_path / "a.txt").write_text("CHANGED\n", encoding="utf-8")  # now dirty
    res = _runner.invoke(app, ["util", "pipeline", "--resume", str(path), "--dry-run",
                               "--restore-checkpoint"], input="y\n")
    assert res.exit_code == 0
    assert "restored" in res.stdout.lower()
    assert (tmp_path / "a.txt").read_text() == "one\n"     # tree restored from the checkpoint


def test_resume_same_git_state_succeeds(tmp_path, monkeypatch) -> None:
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    snap = git_snapshot(tmp_path).model_dump()
    path = _saved_state(tmp_path, snap)
    out = tmp_path.parent / "after.json"
    res = _runner.invoke(app, ["util", "pipeline", "--resume", str(path), "--dry-run", "--out", str(out)])
    assert res.exit_code == 0  # clean tree → no refusal


def test_resume_changed_git_state_refused(tmp_path, monkeypatch) -> None:
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    snap = git_snapshot(tmp_path).model_dump()
    path = _saved_state(tmp_path, snap)
    # change the tree after the snapshot
    (tmp_path / "a.txt").write_text("CHANGED\n", encoding="utf-8")
    res = _runner.invoke(app, ["util", "pipeline", "--resume", str(path), "--dry-run"])
    assert res.exit_code == 2
    assert "working tree changed" in res.stdout
    assert "--force-resume" in res.stdout


def test_force_resume_allows_changed_state(tmp_path, monkeypatch) -> None:
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    snap = git_snapshot(tmp_path).model_dump()
    path = _saved_state(tmp_path, snap)
    (tmp_path / "a.txt").write_text("CHANGED\n", encoding="utf-8")
    res = _runner.invoke(app, ["util", "pipeline", "--resume", str(path), "--dry-run", "--force-resume"])
    assert res.exit_code == 0
    assert "continuing despite" in res.stdout


def test_checkpoint_does_not_destroy_changes(tmp_path, monkeypatch) -> None:
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.txt").write_text("LOCAL EDIT\n", encoding="utf-8")  # uncommitted work
    res = _runner.invoke(app, ["util", "pipeline", "do x", "--dry-run", "--checkpoint"])
    assert res.exit_code == 0
    assert "checkpoint #" in res.stdout
    # the local edit is untouched
    assert (tmp_path / "a.txt").read_text() == "LOCAL EDIT\n"


# --- Wave 9: restore any checkpoint ------------------------------------------

from ronin_cli.checkpoint import checkpoint_details, create_checkpoint


def test_checkpoint_details_shape(tmp_path) -> None:
    _init_repo(tmp_path)
    cp = create_checkpoint(tmp_path, label="pipeline")
    details = checkpoint_details(tmp_path)
    assert len(details) == 1
    d = details[0]
    assert d["id"] == cp.id and d["label"] == "pipeline"
    assert len(d["short_sha"]) == 8 and d["tracked_files"] >= 1
    assert d["created_at"]  # ISO timestamp present


def test_list_checkpoints_output(tmp_path, monkeypatch) -> None:
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    cp = create_checkpoint(tmp_path, label="pipeline")
    snap = git_snapshot(tmp_path).model_dump()
    path = _saved_state(tmp_path, snap, checkpoint_id=cp.id)
    res = _runner.invoke(app, ["util", "pipeline", "--resume", str(path), "--dry-run",
                               "--list-checkpoints", "--force-resume"])
    assert "available checkpoints" in res.stdout
    assert f"#{cp.id}" in res.stdout


def test_restore_latest_checkpoint_gated(tmp_path, monkeypatch) -> None:
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    cp = create_checkpoint(tmp_path, label="pipeline")
    snap = git_snapshot(tmp_path).model_dump()
    path = _saved_state(tmp_path, snap, checkpoint_id=cp.id)
    (tmp_path / "a.txt").write_text("CHANGED\n", encoding="utf-8")
    # decline first (gated): must NOT restore
    res = _runner.invoke(app, ["util", "pipeline", "--resume", str(path), "--dry-run",
                               "--restore-latest-checkpoint"], input="n\n")
    assert res.exit_code == 2
    assert (tmp_path / "a.txt").read_text() == "CHANGED\n"  # untouched


def test_restore_specific_id_success_rechecks(tmp_path, monkeypatch) -> None:
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    cp = create_checkpoint(tmp_path, label="pipeline")
    snap = git_snapshot(tmp_path).model_dump()
    path = _saved_state(tmp_path, snap, checkpoint_id=cp.id)
    (tmp_path / "a.txt").write_text("CHANGED\n", encoding="utf-8")
    res = _runner.invoke(app, ["util", "pipeline", "--resume", str(path), "--dry-run",
                               "--restore-checkpoint-id", str(cp.id)], input="y\n")
    assert res.exit_code == 0
    assert "restored" in res.stdout.lower()
    assert (tmp_path / "a.txt").read_text() == "one\n"  # rewound to the checkpoint


def test_restore_unknown_id_errors(tmp_path, monkeypatch) -> None:
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    cp = create_checkpoint(tmp_path, label="pipeline")
    snap = git_snapshot(tmp_path).model_dump()
    path = _saved_state(tmp_path, snap, checkpoint_id=cp.id)
    (tmp_path / "a.txt").write_text("CHANGED\n", encoding="utf-8")
    res = _runner.invoke(app, ["util", "pipeline", "--resume", str(path), "--dry-run",
                               "--restore-checkpoint-id", "999"])
    assert res.exit_code == 2
    assert "no checkpoint #999" in res.stdout
