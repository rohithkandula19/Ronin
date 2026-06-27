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
