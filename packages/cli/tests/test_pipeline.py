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
    state = run_pipeline(cfg, "x", ["architect", "verifier"], stage_runner=runner,
                         auto_verify_enabled=False)
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


# --- Wave 5: acceptance + verdict rendering ----------------------------------

from ronin_cli.pipeline import render_acceptance


def _state_with_verification(verdict, crits):
    st = plan_pipeline("x", ["architect", "verifier"])
    st.stages[0].status = "completed"
    st.stages[1].status = "completed"
    st.stages[1].artifact = {
        "kind": "verification_report", "final_verdict": verdict,
        "acceptance_criteria_status": crits,
    }
    st.verdict = verdict
    from ronin_cli.pipeline import compute_final_verdict
    st.final_verdict = compute_final_verdict(st)
    return st


def test_render_acceptance_buckets() -> None:
    st = _state_with_verification("failed", [
        {"text": "exports csv", "status": "met"},
        {"text": "handles big files", "status": "unmet"},
        {"text": "unicode safe", "status": "unknown"},
    ])
    out = _render(render_acceptance, st)
    assert "Acceptance criteria" in out
    assert "exports csv" in out and "handles big files" in out and "unicode safe" in out


def test_render_result_shows_verdict() -> None:
    st = _state_with_verification("passed", [{"text": "x", "status": "met"}])
    out = _render(render_pipeline_result, st)
    assert "Final Verification" in out
    assert "Final verdict:" in out and "PASSED" in out


def test_render_acceptance_silent_without_verifier() -> None:
    st = plan_pipeline("x", ["architect"])
    st.stages[0].status = "completed"
    assert _render(render_acceptance, st) == ""


# --- Wave 5: CLI commit/PR gating --------------------------------------------

def test_cli_commit_dry_run_describes_but_does_not_commit(tmp_path, monkeypatch) -> None:
    import subprocess
    for args in (("init", "-q"), ("config", "user.email", "t@t.t"), ("config", "user.name", "t")):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)
    (tmp_path / "f.txt").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "-m", "init"], check=True, capture_output=True)
    monkeypatch.chdir(tmp_path)
    res = _runner.invoke(app, ["pipeline", "do x", "--dry-run", "--commit"])
    assert res.exit_code == 0
    assert "would:" in res.stdout and "commit" in res.stdout
    # still only the initial commit
    n = subprocess.run(["git", "-C", str(tmp_path), "rev-list", "--count", "HEAD"],
                       capture_output=True, text=True).stdout.strip()
    assert n == "1"


# --- Wave 6: final verdict precedence + independent verify + contract --------

from ronin_cli.pipeline import compute_final_verdict, truth_table


def _completed_state(roles, verdict="", iv=None, contract=None, crits=None):
    st = plan_pipeline("x", roles)
    for s in st.stages:
        s.status = "completed"
    if "verifier" in roles or "tester" in roles:
        role = "verifier" if "verifier" in roles else "tester"
        st.stage(role).artifact = {
            "kind": "verification_report", "final_verdict": verdict,
            "acceptance_criteria_status": crits or [],
        }
    st.verdict = verdict
    if iv is not None:
        st.independent_verify = iv
    if contract is not None:
        st.contract = contract
    return st


def test_final_verdict_independent_fail_overrides_tester_pass() -> None:
    st = _completed_state(["implementer", "tester"], verdict="passed",
                          iv={"requested": True, "ran": True, "passed": False})
    assert compute_final_verdict(st) == "failed"


def test_final_verdict_independent_pass_is_sufficient() -> None:
    st = _completed_state(["implementer", "verifier"], verdict="unknown",
                          iv={"requested": True, "ran": True, "passed": True})
    assert compute_final_verdict(st) == "passed"  # real verification wins over 'unknown'


def test_final_verdict_declined_verify_is_blocked() -> None:
    st = _completed_state(["verifier"], verdict="passed",
                          iv={"requested": True, "declined": True})
    assert compute_final_verdict(st) == "blocked"


def test_final_verdict_advisory_mode_preserved() -> None:
    # no verify command → advisory verifier 'passed' still passes
    st = _completed_state(["architect", "verifier"], verdict="passed")
    assert compute_final_verdict(st) == "passed"


def test_final_verdict_contract_failed_is_failed() -> None:
    st = _completed_state(["verifier"], verdict="passed",
                          contract={"final_contract_status": "failed"})
    assert compute_final_verdict(st) == "failed"


def test_final_verdict_blocking_review_fails() -> None:
    st = _completed_state(["reviewer", "verifier"], verdict="passed",
                          contract={"final_contract_status": "failed",
                                    "blocking_issues": ["unresolved review fix: x"]})
    assert compute_final_verdict(st) == "failed"


def test_final_verdict_unmet_criterion_fails() -> None:
    st = _completed_state(["verifier"], verdict="passed",
                          crits=[{"text": "x", "status": "unmet"}])
    assert compute_final_verdict(st) == "failed"


def test_final_verdict_blocked_stage_wins() -> None:
    st = plan_pipeline("x", ["architect", "implementer"])
    st.stages[0].status = "completed"
    st.stages[1].status = "blocked"
    assert compute_final_verdict(st) == "blocked"


def test_truth_table_fields() -> None:
    st = _completed_state(["implementer", "verifier"], verdict="passed",
                          iv={"requested": True, "ran": True, "passed": True},
                          contract={"final_contract_status": "passed"},
                          crits=[{"text": "a", "status": "met"}])
    st.final_verdict = compute_final_verdict(st)
    t = truth_table(st)
    assert t["tests_run"] == "yes"
    assert t["verify_result"] == "passed"
    assert "1 met" in t["acceptance"]
    assert t["contract"] == "passed"
    assert t["semantic_contract"] == "disabled"  # not enabled in this state
    assert t["review_blockers"] == "none"
    assert t["final_verdict"] == "passed"


def test_run_pipeline_independent_verify_failing_sets_failed() -> None:
    # tester says passed, but the injected verify command fails → final verdict failed
    def runner(config, role, prompt, *, read_only, root, console, max_iterations):
        art = None
        if role == "tester":
            art = {"kind": "verification_report", "final_verdict": "passed", "tests_run": ["t1"]}
        return StageOutcome(success=True, summary=f"{role} ok", artifact=art)

    import ronin_cli.pipeline_verify as pv
    from ronin_cli.pipeline_verify import VerifyRun

    def fake_verify(command, root, **kw):
        return VerifyRun(requested=True, command=command, ran=True, passed=False, exit_code=1)

    cfg = RoninConfig(provider="cerebras")
    import unittest.mock as mock
    with mock.patch.object(pv, "independent_verify", fake_verify):
        state = run_pipeline(cfg, "x", ["tester"], stage_runner=runner,
                             verify_cmd="pytest -q")
    assert state.independent_verify["passed"] is False
    assert state.final_verdict == "failed"


def test_run_pipeline_no_verify_cmd_preserves_advisory() -> None:
    def runner(config, role, prompt, *, read_only, root, console, max_iterations):
        art = {"kind": "verification_report", "final_verdict": "passed",
               "tests_run": ["t1"]} if role == "verifier" else None
        return StageOutcome(success=True, summary=f"{role} ok", artifact=art)

    cfg = RoninConfig(provider="cerebras")
    state = run_pipeline(cfg, "x", ["architect", "verifier"], stage_runner=runner,
                         auto_verify_enabled=False)
    assert state.independent_verify == {}          # nothing ran
    assert state.final_verdict == "passed"          # advisory preserved


# --- Wave 6: CLI flags (resume / verify / json shape / gating) ---------------

def test_cli_no_task_no_resume_exits_2(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    res = _runner.invoke(app, ["pipeline"])
    assert res.exit_code == 2
    assert "give a task" in res.stdout or "--resume" in res.stdout


def test_cli_resume_corrupt_file_exits_2(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    res = _runner.invoke(app, ["pipeline", "--resume", str(bad)])
    assert res.exit_code == 2
    assert "corrupt" in res.stdout.lower() or "incompatible" in res.stdout.lower()


def test_cli_resume_continues_from_incomplete(tmp_path, monkeypatch) -> None:
    import json as _json
    monkeypatch.chdir(tmp_path)
    # a saved state: architect done, implementer failed, verifier pending
    saved = plan_pipeline("resume me", ["architect", "implementer", "verifier"])
    saved.stages[0].status = "completed"
    saved.stages[0].artifact = {"kind": "architect"}
    saved.stages[1].status = "failed"
    saved.root = str(tmp_path)
    path = tmp_path / "st.json"
    path.write_text(saved.model_dump_json(), encoding="utf-8")
    out = tmp_path / "after.json"
    # resume in dry-run so no model is needed; it should load + re-render the plan
    res = _runner.invoke(app, ["pipeline", "--resume", str(path), "--dry-run", "--out", str(out)])
    assert res.exit_code == 0
    data = _json.loads(out.read_text())
    assert data["task"] == "resume me"
    assert data["roles"] == ["architect", "implementer", "verifier"]


def test_cli_json_state_has_wave6_fields(tmp_path, monkeypatch) -> None:
    import json as _json
    monkeypatch.chdir(tmp_path)
    res = _runner.invoke(app, ["pipeline", "do x", "--dry-run", "--json"])
    data = _json.loads(res.stdout)
    for key in ("contract", "independent_verify", "final_verdict", "verdict", "root"):
        assert key in data


# --- Wave 7: auto-detected verification --------------------------------------

def test_auto_verify_failure_overrides_tester(tmp_path, monkeypatch) -> None:
    # a python repo so detection finds pytest
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()

    def runner(config, role, prompt, *, read_only, root, console, max_iterations):
        art = {"kind": "verification_report", "final_verdict": "passed",
               "tests_run": ["t1"]} if role == "tester" else None
        return StageOutcome(success=True, summary=f"{role} ok", artifact=art)

    import ronin_cli.pipeline_verify as pv
    from ronin_cli.pipeline_verify import VerifyRun

    def fake_verify(command, root, **kw):
        return VerifyRun(requested=True, command=command, ran=True, passed=False, exit_code=1)

    cfg = RoninConfig(provider="cerebras")
    import unittest.mock as mock
    with mock.patch.object(pv, "independent_verify", fake_verify):
        state = run_pipeline(cfg, "x", ["tester"], stage_runner=runner, root=tmp_path)
    assert state.verify_source == "detected"
    assert state.independent_verify["passed"] is False
    assert state.final_verdict == "failed"   # auto-verify failure overrides tester


def test_no_auto_verify_disables_detection(tmp_path) -> None:
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()

    def runner(config, role, prompt, *, read_only, root, console, max_iterations):
        art = {"kind": "verification_report", "final_verdict": "passed",
               "tests_run": ["t1"]} if role == "verifier" else None
        return StageOutcome(success=True, summary=f"{role} ok", artifact=art)

    cfg = RoninConfig(provider="cerebras")
    state = run_pipeline(cfg, "x", ["architect", "verifier"], stage_runner=runner,
                         root=tmp_path, auto_verify_enabled=False)
    assert state.independent_verify == {}        # nothing ran
    assert state.verify_source == "not_found"
    assert state.final_verdict == "passed"        # advisory verifier preserved


def test_no_detected_command_preserves_advisory(tmp_path) -> None:
    # empty dir → no command detected → advisory verifier still passes
    def runner(config, role, prompt, *, read_only, root, console, max_iterations):
        art = {"kind": "verification_report", "final_verdict": "passed",
               "tests_run": ["t1"]} if role == "verifier" else None
        return StageOutcome(success=True, summary=f"{role} ok", artifact=art)

    cfg = RoninConfig(provider="cerebras")
    state = run_pipeline(cfg, "x", ["verifier"], stage_runner=runner, root=tmp_path)
    assert state.verify_source == "not_found"
    assert state.final_verdict == "passed"


# --- Wave 7: semantic contract integration + truth table ---------------------

def test_semantic_failed_forces_final_failed() -> None:
    st = _completed_state(["implementer", "verifier"], verdict="passed",
                          iv={"requested": True, "ran": True, "passed": True},
                          contract={"final_contract_status": "passed"})
    st.semantic = {"final_semantic_status": "failed",
                   "objective_alignment": "misaligned", "evidence": ["touches unrelated files"]}
    assert compute_final_verdict(st) == "failed"


def test_semantic_warning_is_advisory_not_failing() -> None:
    st = _completed_state(["implementer", "verifier"], verdict="passed",
                          iv={"requested": True, "ran": True, "passed": True},
                          contract={"final_contract_status": "passed"})
    st.semantic = {"final_semantic_status": "warning"}
    assert compute_final_verdict(st) == "passed"  # warning doesn't block


def test_truth_table_has_wave7_rows() -> None:
    st = _completed_state(["implementer", "verifier"], verdict="passed",
                          iv={"requested": True, "ran": True, "passed": True})
    st.verify_source = "detected"
    st.git_snapshot = {"dirty_state": False}
    st.semantic = {"final_semantic_status": "passed"}
    st.final_verdict = compute_final_verdict(st)
    t = truth_table(st)
    assert t["verify_command"] == "detected"
    assert t["git_snapshot"] == "clean"
    assert t["semantic_contract"] == "passed"


def test_run_pipeline_semantic_enabled_records_report() -> None:
    def runner(config, role, prompt, *, read_only, root, console, max_iterations):
        art = None
        if role == "implementer":
            art = {"kind": "implementation_report", "files_changed": ["x.py"]}
        if role == "verifier":
            art = {"kind": "verification_report", "final_verdict": "passed", "tests_run": ["t"]}
        return StageOutcome(success=True, summary=f"{role} ok", artifact=art)

    import ronin_cli.pipeline as pl
    # inject the judge by patching check_semantic_contract's runner indirectly:
    import ronin_cli.runner as rn
    from ronin_agent_patterns import FakeProvider, LLMResponse
    prov = FakeProvider(responses=[LLMResponse(
        text='```json\n{"objective_alignment":"aligned","acceptance_alignment":"aligned",'
             '"scope_creep_risk":"none","evidence":["did the thing"]}\n```',
        stop_reason="end_turn", usage={"input_tokens": 1, "output_tokens": 1})])
    import pathlib
    import subprocess as _sp
    import tempfile

    import unittest.mock as mock
    cfg = RoninConfig(provider="cerebras")
    # deterministic full diff: a tmp git repo with a small uncommitted change, so
    # the semantic 'passed' isn't downgraded for a missing/truncated diff.
    with tempfile.TemporaryDirectory() as td:
        for args in (("init", "-q"), ("config", "user.email", "t@t.t"), ("config", "user.name", "t")):
            _sp.run(["git", "-C", td, *args], check=True, capture_output=True)
        (pathlib.Path(td) / "x.py").write_text("v = 1\n", encoding="utf-8")
        _sp.run(["git", "-C", td, "add", "-A"], check=True, capture_output=True)
        _sp.run(["git", "-C", td, "commit", "-q", "-m", "init"], check=True, capture_output=True)
        (pathlib.Path(td) / "x.py").write_text("v = 2\n", encoding="utf-8")  # real, small diff
        with mock.patch.object(rn, "build_provider", return_value=prov):
            state = run_pipeline(cfg, "x", ["implementer", "verifier"], stage_runner=runner,
                                 root=td, auto_verify_enabled=False, semantic_enabled=True)
    assert state.diff_evidence.get("full_diff_available") is True
    assert state.semantic.get("final_semantic_status") == "passed"
    assert state.semantic.get("parsed") is True


# --- Wave 8: diff evidence capture in run_pipeline ---------------------------

def test_run_pipeline_captures_diff_evidence(tmp_path) -> None:
    import subprocess as _sp
    for args in (("init", "-q"), ("config", "user.email", "t@t.t"), ("config", "user.name", "t")):
        _sp.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    _sp.run(["git", "-C", str(tmp_path), "add", "-A"], check=True, capture_output=True)
    _sp.run(["git", "-C", str(tmp_path), "commit", "-q", "-m", "init"], check=True, capture_output=True)
    (tmp_path / "a.py").write_text("x = 1\ny = 2\n", encoding="utf-8")  # a real change

    def runner(config, role, prompt, *, read_only, root, console, max_iterations):
        return StageOutcome(success=True, summary=f"{role} ok")

    cfg = RoninConfig(provider="cerebras")
    state = run_pipeline(cfg, "x", ["architect"], stage_runner=runner, root=tmp_path,
                         auto_verify_enabled=False)
    assert state.diff_evidence["captured"] is True
    assert "a.py" in state.diff_evidence["files_changed"]
    assert state.diff_evidence["full_diff_available"] is True


def test_run_pipeline_no_diff_evidence(tmp_path) -> None:
    def runner(config, role, prompt, *, read_only, root, console, max_iterations):
        return StageOutcome(success=True, summary=f"{role} ok")
    cfg = RoninConfig(provider="cerebras")
    state = run_pipeline(cfg, "x", ["architect"], stage_runner=runner, root=tmp_path,
                         auto_verify_enabled=False, diff_evidence_enabled=False)
    assert state.diff_evidence["captured"] is False
    assert "disabled" in state.diff_evidence["note"]


# --- Wave 8: multi-suite in run_pipeline + truth table -----------------------

def test_run_pipeline_multi_suite_failure_fails(tmp_path) -> None:
    def runner(config, role, prompt, *, read_only, root, console, max_iterations):
        art = {"kind": "verification_report", "final_verdict": "passed", "tests_run": ["t"]} \
            if role == "verifier" else None
        return StageOutcome(success=True, summary=f"{role} ok", artifact=art)

    import ronin_cli.pipeline_verify as pv
    from ronin_cli.pipeline_verify import SuiteRun

    def fake_run_suites(suites, root, **kw):
        return [SuiteRun(name="unit", command="pytest", status="passed", exit_code=0),
                SuiteRun(name="lint", command="ruff", status="failed", exit_code=1)]

    import unittest.mock as mock
    cfg = RoninConfig(provider="cerebras")
    with mock.patch.object(pv, "run_suites", fake_run_suites):
        state = run_pipeline(cfg, "x", ["verifier"], stage_runner=runner, root=tmp_path,
                             verify_suites=["unit:pytest", "lint:ruff"])
    assert len(state.suites) == 2
    assert state.verify_source == "provided"
    assert state.final_verdict == "failed"   # a failing suite fails the run
    t = truth_table(state)
    assert "required 1 passed/1 failed" in t["suites"]


def test_truth_table_diff_evidence_cell() -> None:
    st = _completed_state(["verifier"], verdict="passed")
    st.diff_evidence = {"captured": True, "full_diff_available": True, "truncated": False}
    assert truth_table(st)["diff_evidence"] == "available"
    st.diff_evidence = {"captured": True, "full_diff_available": True, "truncated": True}
    assert truth_table(st)["diff_evidence"] == "truncated"
    st.diff_evidence = {"captured": False, "note": "diff evidence disabled"}
    assert truth_table(st)["diff_evidence"] == "disabled"
    st.diff_evidence = {"captured": False, "note": "not a git repository"}
    assert truth_table(st)["diff_evidence"] == "missing"
