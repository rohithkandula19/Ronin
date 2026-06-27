"""Tests for the Wave 4 role-handoff pipeline — pure core (state / parse / plan)."""
from __future__ import annotations

import pytest

from ronin_cli.config import RoninConfig
from ronin_cli.offline import apply_free, apply_offline
from ronin_cli.pipeline import (
    DEFAULT_ROLES,
    PipelineStage,
    PipelineState,
    parse_roles,
    plan_pipeline,
    stage_permission_label,
    stage_read_only,
)


# --- role parsing ------------------------------------------------------------

def test_default_role_sequence() -> None:
    assert DEFAULT_ROLES == ["architect", "implementer", "reviewer", "tester", "verifier"]
    assert parse_roles(None) == DEFAULT_ROLES
    assert parse_roles("") == DEFAULT_ROLES
    assert parse_roles("  ") == DEFAULT_ROLES


def test_custom_role_sequence_parses() -> None:
    assert parse_roles("debugger,implementer,tester") == ["debugger", "implementer", "tester"]
    assert parse_roles(" reviewer , tester ") == ["reviewer", "tester"]


def test_invalid_role_raises_with_valid_set() -> None:
    with pytest.raises(ValueError, match="unknown role"):
        parse_roles("architect,wizard,tester")


# --- per-stage permissions ---------------------------------------------------

def test_read_only_roles_always_read_only() -> None:
    for role in ("architect", "reviewer", "researcher"):
        assert stage_read_only(role, write_capable=True) is True
        assert stage_read_only(role, write_capable=False) is True


def test_doer_roles_read_only_unless_write_capable() -> None:
    for role in ("implementer", "tester", "debugger"):
        assert stage_read_only(role, write_capable=False) is True   # proposal mode
        assert stage_read_only(role, write_capable=True) is False    # may act (gated)


def test_permission_labels() -> None:
    assert stage_permission_label("architect", write_capable=True) == "read-only"
    assert stage_permission_label("implementer", write_capable=False) == "read-only (proposal)"
    assert stage_permission_label("implementer", write_capable=True) == "edits + commands (gated)"
    assert stage_permission_label("tester", write_capable=True) == "runs tests (gated)"


# --- state model -------------------------------------------------------------

def test_plan_pipeline_all_pending() -> None:
    st = plan_pipeline("do a thing", DEFAULT_ROLES, provider="cerebras", badge="FREE")
    assert [s.role for s in st.stages] == DEFAULT_ROLES
    assert all(s.status == "pending" for s in st.stages)
    assert st.badge == "FREE" and st.provider == "cerebras"
    assert st.stopped is False
    assert st.outcome() == "partial"


def test_state_stopped_and_outcome() -> None:
    st = plan_pipeline("t", ["architect", "implementer"])
    st.stages[0].status = "completed"
    st.stages[1].status = "failed"
    assert st.stopped is True
    assert st.outcome() == "failed"


def test_dry_run_outcome_is_planned() -> None:
    st = plan_pipeline("t", DEFAULT_ROLES, dry_run=True)
    assert st.outcome() == "planned"


def test_state_serializes_to_json() -> None:
    st = plan_pipeline("ship it", ["reviewer", "tester"], badge="LOCAL", offline=True)
    st.stages[0].status = "completed"
    st.stages[0].summary = "no blocking issues"
    st.stages[0].files_changed = []
    data = st.model_dump()
    assert data["task"] == "ship it"
    assert data["roles"] == ["reviewer", "tester"]
    assert data["badge"] == "LOCAL" and data["offline"] is True
    assert data["stages"][0]["status"] == "completed"
    # round-trips
    again = PipelineState.model_validate_json(st.model_dump_json())
    assert again.roles == st.roles
    assert again.stages[0].summary == "no blocking issues"


# --- shared free/offline resolution ------------------------------------------

def test_apply_free_keeps_already_free_provider() -> None:
    cfg = RoninConfig(provider="cerebras")
    assert apply_free(cfg).provider == "cerebras"


def _clear_shared_keys(monkeypatch) -> None:
    # free providers share the OPENAI_API_KEY slot; clear it so only explicit
    # per-provider keys count (deterministic across machines).
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GROQ_API_KEY",
                "CEREBRAS_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(var, raising=False)


def test_apply_free_falls_back_to_keyless_local(monkeypatch) -> None:
    _clear_shared_keys(monkeypatch)
    cfg = RoninConfig(provider="anthropic")  # paid, no free key
    assert apply_free(cfg).provider == "local"


def test_apply_free_prefers_keyed_free_tier(monkeypatch) -> None:
    _clear_shared_keys(monkeypatch)
    cfg = RoninConfig(provider="anthropic")
    # only groq has a per-provider key (set_key_for would mirror into the shared
    # OpenAI slot and unlock every free provider, so set it in isolation here)
    cfg.provider_keys["groq"] = "sk-test"
    assert apply_free(cfg).provider == "groq"


def test_apply_free_picks_first_preference_with_shared_key(monkeypatch) -> None:
    _clear_shared_keys(monkeypatch)
    cfg = RoninConfig(provider="anthropic")
    cfg.set_key_for("groq", "sk-test")  # mirrors to shared slot → all free tiers usable
    assert apply_free(cfg).provider == "cerebras"  # first in FREE_PREFERENCE


def test_apply_offline_forces_local_brain() -> None:
    cfg = RoninConfig(provider="anthropic", offline=True)
    assert apply_offline(cfg).provider == "ollama"


# --- renderers ---------------------------------------------------------------

import io
from rich.console import Console

from ronin_cli.pipeline import (
    render_pipeline,
    render_pipeline_plan,
    render_pipeline_result,
    stage_line,
)


def _render(fn, state, width=80) -> str:
    buf = io.StringIO()
    fn(Console(file=buf, force_terminal=False, width=width), state)
    return buf.getvalue()


def test_render_pipeline_live_states() -> None:
    st = plan_pipeline("fix bug", DEFAULT_ROLES)
    st.stages[0].status = "completed"; st.stages[0].summary = "plan created"
    st.stages[1].status = "active"
    out = _render(render_pipeline, st)
    assert "Pipeline" in out
    assert "✓ architect" in out and "plan created" in out
    assert "▶ implementer" in out
    assert "☐ reviewer" in out and "waiting" in out


def test_render_blocked_and_failed_states() -> None:
    st = plan_pipeline("x", ["implementer", "tester"])
    st.stages[0].status = "failed"; st.stages[0].summary = "patch did not apply"
    st.stages[1].status = "blocked"
    out = _render(render_pipeline, st)
    assert "✗ implementer" in out and "patch did not apply" in out
    assert "⊘ tester" in out and "blocked" in out


def test_render_result_lists_each_stage_and_final() -> None:
    st = plan_pipeline("ship", ["architect", "reviewer"])
    st.stages[0].status = "completed"; st.stages[0].summary = "designed modules"
    st.stages[1].status = "completed"; st.stages[1].summary = "no blocking issues"
    st.final_recommendation = "safe to merge"
    out = _render(render_pipeline_result, st)
    assert "Pipeline result" in out
    assert "Architect:" in out and "designed modules" in out
    assert "Reviewer:" in out and "no blocking issues" in out
    assert "Final:" in out and "safe to merge" in out


def test_dry_run_plan_shows_sequence_and_permissions() -> None:
    st = plan_pipeline("do x", DEFAULT_ROLES, write_capable=False,
                       provider="cerebras", model="gpt-oss-120b", badge="FREE", dry_run=True)
    out = _render(render_pipeline_plan, st)
    assert "dry run" in out and "READ-ONLY" in out
    assert "FREE" in out and "cerebras/gpt-oss-120b" in out
    for role in DEFAULT_ROLES:
        assert role in out
    assert "read-only (proposal)" in out  # implementer in read-only pipeline


def test_dry_run_plan_write_capable_label() -> None:
    st = plan_pipeline("do x", ["implementer"], write_capable=True, dry_run=True)
    out = _render(render_pipeline_plan, st)
    assert "WRITE-CAPABLE" in out
    assert "edits + commands (gated)" in out


def test_stage_line_truncates_in_narrow_terminal() -> None:
    st = PipelineStage(role="implementer", status="active",
                       summary="proposing a very long diff across many files and modules here")
    line = stage_line(st, width=30)
    # the visible text (strip rich tags) stays within budget
    import re
    visible = re.sub(r"\[/?[^\]]+\]", "", line)
    assert len(visible) <= 32  # width + small slack for the indent
    assert "implementer" in visible
    assert "…" in visible  # detail was trimmed


# --- orchestration (injected stage runner) -----------------------------------

from ronin_cli.pipeline import StageOutcome, run_pipeline


def _recording_runner(calls, *, fail_on=None, block_on=None):
    def runner(config, role, prompt, *, read_only, root, console, max_iterations):
        calls.append({"role": role, "read_only": read_only, "prompt": prompt})
        if block_on == role:
            return StageOutcome(success=False, summary=f"{role} blocked", blocked=True)
        if fail_on == role:
            return StageOutcome(success=False, summary=f"{role} failed")
        return StageOutcome(success=True, summary=f"{role} ok",
                            artifact={"kind": f"{role}_artifact", "marker": f"{role}-did-it"})
    return runner


def test_pipeline_runs_stages_in_order_with_correct_readonly() -> None:
    calls: list = []
    cfg = RoninConfig(provider="cerebras")
    state = run_pipeline(cfg, "do x", DEFAULT_ROLES, write=False,
                         stage_runner=_recording_runner(calls))
    assert [c["role"] for c in calls] == DEFAULT_ROLES        # sequential, in order
    # read-only roles always RO; doer roles RO too without --write
    assert all(c["read_only"] for c in calls)
    assert state.outcome() == "completed"
    assert all(s.status == "completed" for s in state.stages)
    # handoff: the architect's structured artifact is passed into the next prompt
    assert "architect-did-it" in calls[1]["prompt"]
    # and each stage stores its artifact on the state (so --json includes them)
    assert state.stage("architect").artifact["marker"] == "architect-did-it"


def test_write_capable_lets_doers_act_but_keeps_readonly_roles_locked() -> None:
    calls: list = []
    cfg = RoninConfig(provider="anthropic")
    run_pipeline(cfg, "do x", ["architect", "implementer", "reviewer", "tester"],
                 write=True, stage_runner=_recording_runner(calls))
    ro = {c["role"]: c["read_only"] for c in calls}
    assert ro["architect"] is True and ro["reviewer"] is True   # always read-only
    assert ro["implementer"] is False and ro["tester"] is False  # may act (still gated)


def test_pipeline_stops_on_failure_and_skips_rest() -> None:
    calls: list = []
    cfg = RoninConfig(provider="cerebras")
    state = run_pipeline(cfg, "x", DEFAULT_ROLES,
                         stage_runner=_recording_runner(calls, fail_on="implementer"))
    assert [c["role"] for c in calls] == ["architect", "implementer"]  # halted
    assert state.stage("implementer").status == "failed"
    assert state.stage("reviewer").status == "skipped"
    assert state.stage("tester").status == "skipped"
    assert state.outcome() == "failed"
    assert "stopped at implementer" in state.final_recommendation


def test_pipeline_blocked_stage_halts() -> None:
    calls: list = []
    cfg = RoninConfig(provider="cerebras")
    state = run_pipeline(cfg, "x", ["architect", "implementer", "tester"],
                         stage_runner=_recording_runner(calls, block_on="implementer"))
    assert state.stage("implementer").status == "blocked"
    assert state.stage("tester").status == "skipped"
    assert state.outcome() == "blocked"


def test_dry_run_runs_no_stages() -> None:
    def boom(*a, **k):
        raise AssertionError("stage runner must not be called in --dry-run")
    cfg = RoninConfig(provider="cerebras")
    state = run_pipeline(cfg, "x", DEFAULT_ROLES, dry_run=True, stage_runner=boom)
    assert state.outcome() == "planned"
    assert all(s.status == "pending" for s in state.stages)


def test_free_flag_sets_free_badge(monkeypatch) -> None:
    _clear_shared_keys(monkeypatch)
    cfg = RoninConfig(provider="anthropic")  # paid → free resolves to keyless local
    state = run_pipeline(cfg, "x", ["architect"], free=True,
                         stage_runner=_recording_runner([]))
    # local keyless brain → LOCAL badge (still $0, never a paid API)
    assert state.badge in ("FREE", "LOCAL")
    assert state.provider in ("local", "cerebras", "groq", "gemini", "openrouter")


def test_offline_flag_sets_local_badge() -> None:
    cfg = RoninConfig(provider="anthropic")
    state = run_pipeline(cfg, "x", ["architect"], offline=True,
                         stage_runner=_recording_runner([]))
    assert state.badge == "LOCAL"
    assert state.offline is True
    assert state.provider == "ollama"


def test_default_runner_enforces_readonly_role_no_writes(tmp_path, monkeypatch) -> None:
    """Integration: the real stage runner + a read-only role makes no edits."""
    from unittest.mock import patch

    from ronin_agent_patterns import FakeProvider, LLMResponse, ToolCall
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    (tmp_path / "f.py").write_text("x = 1\n", encoding="utf-8")
    # architect (read-only) tries to write — the tool isn't available, file stays
    prov = FakeProvider(responses=[
        LLMResponse(text="planning", stop_reason="tool_use",
                    tool_calls=[ToolCall(id="t1", name="write_file",
                                         arguments={"path": "f.py", "content": "x = 2\n"})],
                    usage={"input_tokens": 5, "output_tokens": 2}),
        LLMResponse(text="Here is the plan: change x to 2.", stop_reason="end_turn",
                    usage={"input_tokens": 5, "output_tokens": 2}),
    ])
    cfg = RoninConfig(provider="anthropic")
    with patch("ronin_cli.code_mode.build_provider", return_value=prov):
        state = run_pipeline(cfg, "bump x", ["architect"], write=True, root=tmp_path,
                             console=None, max_iterations=4)
    assert (tmp_path / "f.py").read_text() == "x = 1\n"   # architect can't write
    assert "write_file" not in prov.calls[0]["tool_names"]
    assert state.stages[0].status in ("completed", "failed")


# --- CLI command (ronin pipeline) --------------------------------------------

from typer.testing import CliRunner

from ronin_cli.main import app

_runner = CliRunner()


def test_cli_dry_run_shows_plan_and_edits_nothing(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "keep.py").write_text("v = 1\n", encoding="utf-8")
    res = _runner.invoke(app, ["pipeline", "add a feature", "--dry-run"])
    assert res.exit_code == 0
    assert "Pipeline plan" in res.stdout and "dry run" in res.stdout
    assert "READ-ONLY" in res.stdout
    for role in ("architect", "implementer", "reviewer", "tester"):
        assert role in res.stdout
    # nothing was edited
    assert (tmp_path / "keep.py").read_text() == "v = 1\n"


def test_cli_dry_run_json_shape(tmp_path, monkeypatch) -> None:
    import json as _json
    monkeypatch.chdir(tmp_path)
    res = _runner.invoke(app, ["pipeline", "do x", "--dry-run", "--json",
                               "--roles", "reviewer,tester"])
    assert res.exit_code == 0
    data = _json.loads(res.stdout)
    assert data["task"] == "do x"
    assert data["roles"] == ["reviewer", "tester"]
    assert [s["status"] for s in data["stages"]] == ["pending", "pending"]
    assert data["dry_run"] is True


def test_cli_invalid_roles_exit_2(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    res = _runner.invoke(app, ["pipeline", "x", "--roles", "architect,wizard"])
    assert res.exit_code == 2
    assert "unknown role" in res.stdout


def test_cli_out_writes_json_file(tmp_path, monkeypatch) -> None:
    import json as _json
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "state.json"
    res = _runner.invoke(app, ["pipeline", "ship", "--dry-run", "--out", str(out)])
    assert res.exit_code == 0
    assert out.exists()
    data = _json.loads(out.read_text())
    assert data["task"] == "ship" and data["dry_run"] is True


def test_cli_dry_run_write_flag_shows_write_capable(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    res = _runner.invoke(app, ["pipeline", "x", "--dry-run", "--write",
                               "--roles", "implementer"])
    assert res.exit_code == 0
    assert "WRITE-CAPABLE" in res.stdout


# --- Wave 5: artifact handoff + verdict --------------------------------------

def test_state_verdict_from_verifier_artifact() -> None:
    def runner(config, role, prompt, *, read_only, root, console, max_iterations):
        art = None
        if role == "verifier":
            art = {"kind": "verification_report", "final_verdict": "passed",
                   "tests_run": ["t1"],
                   "acceptance_criteria_status": [{"text": "exports csv", "status": "met"}]}
        return StageOutcome(success=True, summary=f"{role} ok", artifact=art)

    cfg = RoninConfig(provider="cerebras")
    state = run_pipeline(cfg, "x", ["architect", "verifier"], stage_runner=runner)
    assert state.verdict == "passed"
    assert state.acceptance_summary()["met"] == ["exports csv"]
    assert "PASSED" in state.final_recommendation


def test_artifacts_preserved_when_a_later_stage_fails() -> None:
    def runner(config, role, prompt, *, read_only, root, console, max_iterations):
        if role == "reviewer":
            return StageOutcome(success=False, summary="reviewer failed")
        return StageOutcome(success=True, summary=f"{role} ok",
                            artifact={"kind": f"{role}", "ok": True})

    cfg = RoninConfig(provider="cerebras")
    state = run_pipeline(cfg, "x", ["architect", "implementer", "reviewer", "verifier"],
                         stage_runner=runner)
    # earlier artifacts survive the failure
    assert state.stage("architect").artifact == {"kind": "architect", "ok": True}
    assert state.stage("implementer").artifact == {"kind": "implementer", "ok": True}
    assert state.stage("reviewer").status == "failed"
    assert state.stage("verifier").status == "skipped"


def test_json_output_includes_artifacts(tmp_path, monkeypatch) -> None:
    import json as _json
    monkeypatch.chdir(tmp_path)
    res = _runner.invoke(app, ["pipeline", "do x", "--dry-run", "--json"])
    data = _json.loads(res.stdout)
    # dry-run stages carry the artifact field (empty dict), so the shape is stable
    assert "artifact" in data["stages"][0]
    assert "verdict" in data


def test_default_runner_builds_architect_plan_artifact(tmp_path, monkeypatch) -> None:
    from unittest.mock import patch

    from ronin_agent_patterns import FakeProvider, LLMResponse
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    plan_json = ('Here is the plan.\n```json\n'
                 '{"objective": "add CSV export", "files_to_change": ["export.py"], '
                 '"acceptance_criteria": [{"id":"ac1","text":"CSV downloads","status":"unknown"}]}\n```')
    prov = FakeProvider(responses=[
        LLMResponse(text=plan_json, stop_reason="end_turn",
                    usage={"input_tokens": 5, "output_tokens": 2}),
    ])
    cfg = RoninConfig(provider="anthropic")
    with patch("ronin_cli.code_mode.build_provider", return_value=prov):
        state = run_pipeline(cfg, "add CSV export", ["architect"], root=tmp_path,
                             console=None, max_iterations=3)
    plan = state.architect_plan()
    assert plan is not None and plan.parsed is True
    assert plan.objective == "add CSV export"
    assert plan.files_to_change == ["export.py"]
    assert plan.acceptance_criteria[0].text == "CSV downloads"
