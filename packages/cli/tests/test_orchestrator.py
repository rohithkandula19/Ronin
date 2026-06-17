"""Tests for the multi-agent Orchestrator.

Everything runs OFFLINE: the planner and synthesizer use ``FakeProvider`` (canned
responses, no network), and the subagent runner ``run_code_agent`` is
monkeypatched with a fake. The single test that exercises real git-worktree
isolation builds a throwaway local repo — no LLM, no keys, no spend. These tests
cover the orchestration (decomposition, provider assignment, level scheduling,
parallel fan-out, worktree isolation, dependency context, synthesis), not any
model's reasoning.
"""
from __future__ import annotations

import subprocess
import threading
from pathlib import Path

from ronin_agent_patterns import FakeProvider, LLMResponse

from ronin_cli import code_mode, orchestrator
from ronin_cli.code_mode import CodeRunResult
from ronin_cli.config import RoninConfig
from ronin_cli.orchestrator import (
    Plan,
    SubTask,
    assign_providers,
    make_plan,
    parse_roster,
    run_orchestrator,
    synthesize,
)


def _cfg() -> RoninConfig:
    return RoninConfig(provider="anthropic", anthropic_api_key="sk-x")


def _plan_response(body: str) -> FakeProvider:
    return FakeProvider(responses=[LLMResponse(text=f"<plan>{body}</plan>")])


def _init_repo(path: Path) -> None:
    def git(*args):
        subprocess.run(["git", "-C", str(path), *args], check=True,
                       capture_output=True, text=True)
    git("init", "-q")
    git("config", "user.email", "t@t.dev")
    git("config", "user.name", "Test")
    git("config", "commit.gpgsign", "false")
    (path / "seed.txt").write_text("seed\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "init")


# ---------- parse_roster (pure) ----------

def test_parse_roster() -> None:
    assert parse_roster("anthropic,gemini") == ["anthropic", "gemini"]
    assert parse_roster("anthropic, cerebras:gpt-oss-120b ") == [
        "anthropic", "cerebras:gpt-oss-120b"]
    assert parse_roster("") == []
    assert parse_roster(None) == []


# ---------- make_plan (planner, injected provider) ----------

def test_make_plan_parses_subtasks() -> None:
    prov = _plan_response(
        '{"goal": "ship a feature", "subtasks": ['
        '{"id": "explore", "kind": "research", "role": "scout", "prompt": "look", "depends_on": []},'
        '{"id": "build", "kind": "edit", "role": "impl", "prompt": "do it", "depends_on": ["explore"]}'
        ']}')
    plan = make_plan(_cfg(), "ship a feature", planner_provider=prov)
    assert plan.goal == "ship a feature"
    assert [s.id for s in plan.subtasks] == ["explore", "build"]
    assert plan.subtasks[0].kind == "research" and not plan.subtasks[0].mutating
    assert plan.subtasks[1].mutating
    # the goal was actually handed to the planner
    assert "ship a feature" in prov.calls[0]["messages"][0]["content"]


def test_make_plan_rejects_missing_block() -> None:
    prov = FakeProvider(responses=[LLMResponse(text="no plan tags here")])
    try:
        make_plan(_cfg(), "x", planner_provider=prov)
    except ValueError as e:
        assert "did not emit" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_make_plan_rejects_empty_subtasks() -> None:
    prov = _plan_response('{"goal": "g", "subtasks": []}')
    try:
        make_plan(_cfg(), "g", planner_provider=prov)
    except ValueError as e:
        assert "no subtasks" in str(e)
    else:
        raise AssertionError("expected ValueError")


# ---------- assign_providers (provider-agnostic, the differentiator) ----------

def test_assign_providers_round_robin() -> None:
    plan = Plan(goal="g", subtasks=[
        SubTask(id="a", prompt="p"),
        SubTask(id="b", prompt="p"),
        SubTask(id="c", prompt="p"),
    ])
    out = assign_providers(plan, ["anthropic", "gemini"])
    assert [s.provider for s in out.subtasks] == ["anthropic", "gemini", "anthropic"]
    # input plan is not mutated (pure)
    assert all(s.provider is None for s in plan.subtasks)


def test_assign_providers_explicit_wins_over_roster() -> None:
    plan = Plan(goal="g", subtasks=[SubTask(id="a", prompt="p", provider="groq:x")])
    out = assign_providers(plan, ["anthropic"])
    assert out.subtasks[0].provider == "groq:x"


def test_assign_providers_empty_roster_is_noop() -> None:
    plan = Plan(goal="g", subtasks=[SubTask(id="a", prompt="p")])
    assert assign_providers(plan, []).subtasks[0].provider is None


# ---------- Plan.levels (pure scheduler) ----------

def test_levels_groups_by_dependency() -> None:
    plan = Plan(goal="g", subtasks=[
        SubTask(id="a", prompt="p"),
        SubTask(id="b", prompt="p"),
        SubTask(id="c", prompt="p", depends_on=["a", "b"]),
    ])
    levels = plan.levels()
    assert {s.id for s in levels[0]} == {"a", "b"}
    assert [s.id for s in levels[1]] == ["c"]


def test_levels_unknown_dep_treated_satisfied() -> None:
    plan = Plan(goal="g", subtasks=[SubTask(id="a", prompt="p", depends_on=["ghost"])])
    levels = plan.levels()
    assert [s.id for s in levels[0]] == ["a"]  # did not deadlock


def test_levels_cycle_terminates() -> None:
    plan = Plan(goal="g", subtasks=[
        SubTask(id="a", prompt="p", depends_on=["b"]),
        SubTask(id="b", prompt="p", depends_on=["a"]),
    ])
    levels = plan.levels()
    # a real cycle still terminates: the stuck pair is emitted as one final level
    assert sum(len(lv) for lv in levels) == 2


# ---------- run_orchestrator (research fan-out, injected providers) ----------

def test_run_orchestrator_research_fanout_and_synthesis(monkeypatch, tmp_path) -> None:
    seen_providers: list[str] = []

    def fake_run(config, prompt, **kwargs):
        seen_providers.append(config.provider)
        assert kwargs["read_only"] is True  # research subagents are read-only
        return CodeRunResult(success=True, output=f"{config.provider} did: {prompt}",
                             iterations=1)

    monkeypatch.setattr(code_mode, "run_code_agent", fake_run)
    plan = Plan(goal="investigate", subtasks=[
        SubTask(id="auth", kind="research", role="auth", prompt="how does auth work"),
        SubTask(id="rate", kind="research", role="rate", prompt="where is rate limiting"),
    ])
    synth = FakeProvider(responses=[LLMResponse(text="FUSED ANSWER")])
    result = run_orchestrator(_cfg(), "investigate", plan=plan, roster=["anthropic", "gemini"],
                              root=tmp_path, synth_provider=synth)

    assert len(result.subresults) == 2
    assert {r.label.split(":")[0] for r in result.subresults} == {"anthropic", "gemini"}
    # each subagent ran on its assigned provider
    assert set(seen_providers) == {"anthropic", "gemini"}
    assert all(r.ok for r in result.subresults)
    assert result.synthesis == "FUSED ANSWER"
    # the fused answer was built from BOTH subagent outputs
    assert "anthropic did" in synth.calls[0]["messages"][0]["content"]
    assert "gemini did" in synth.calls[0]["messages"][0]["content"]


def test_run_orchestrator_runs_a_level_concurrently(monkeypatch, tmp_path) -> None:
    barrier = threading.Barrier(3, timeout=5)

    def fake_run(config, prompt, **kwargs):
        barrier.wait()  # times out -> test fails if not concurrent
        return CodeRunResult(success=True, output="ok", iterations=1)

    monkeypatch.setattr(code_mode, "run_code_agent", fake_run)
    plan = Plan(goal="g", subtasks=[
        SubTask(id=f"t{i}", kind="research", prompt=str(i)) for i in range(3)])
    synth = FakeProvider(responses=[LLMResponse(text="done")])
    result = run_orchestrator(_cfg(), "g", plan=plan, root=tmp_path,
                              synth_provider=synth, max_workers=3)
    assert len(result.succeeded) == 3


def test_run_orchestrator_dependency_context_flows(monkeypatch, tmp_path) -> None:
    prompts: dict[str, str] = {}

    def fake_run(config, prompt, **kwargs):
        # record by trailing token so we can find the dependent subagent's prompt
        prompts[prompt.strip().split()[-1]] = prompt
        return CodeRunResult(success=True, output=f"finding-for-{prompt.strip().split()[-1]}",
                             iterations=1)

    monkeypatch.setattr(code_mode, "run_code_agent", fake_run)
    plan = Plan(goal="g", subtasks=[
        SubTask(id="first", kind="research", role="scout", prompt="explore alpha"),
        SubTask(id="second", kind="research", prompt="use beta", depends_on=["first"]),
    ])
    synth = FakeProvider(responses=[LLMResponse(text="s")])
    run_orchestrator(_cfg(), "g", plan=plan, root=tmp_path, synth_provider=synth)
    # the dependent subagent saw the first subagent's finding in its prompt
    assert "finding-for-alpha" in prompts["beta"]


def test_run_orchestrator_subagent_failure_is_captured(monkeypatch, tmp_path) -> None:
    def fake_run(config, prompt, **kwargs):
        if config.provider == "gemini":
            return CodeRunResult(success=False, output="", iterations=1, error="rate limited")
        return CodeRunResult(success=True, output="good", iterations=1)

    monkeypatch.setattr(code_mode, "run_code_agent", fake_run)
    plan = Plan(goal="g", subtasks=[
        SubTask(id="a", kind="research", prompt="p"),
        SubTask(id="b", kind="research", prompt="p"),
    ])
    synth = FakeProvider(responses=[LLMResponse(text="s")])
    result = run_orchestrator(_cfg(), "g", plan=plan, roster=["anthropic", "gemini"],
                              root=tmp_path, synth_provider=synth)
    by_id = {r.id: r for r in result.subresults}
    assert by_id["a"].ok and not by_id["b"].ok
    assert by_id["b"].error == "rate limited"


# ---------- edit subtasks run isolated in a real git worktree ----------

def test_run_orchestrator_edit_isolated_in_worktree(monkeypatch, tmp_path) -> None:
    _init_repo(tmp_path)
    seen_roots: list[Path] = []

    def fake_run(config, prompt, *, root, read_only, **kwargs):
        assert read_only is False            # edit subagents may mutate
        assert Path(root) != tmp_path        # ...inside an isolated worktree
        seen_roots.append(Path(root))
        (Path(root) / "feature.py").write_text("def feature():\n    pass\n", encoding="utf-8")
        return CodeRunResult(success=True, output="added feature", iterations=2)

    monkeypatch.setattr(code_mode, "run_code_agent", fake_run)
    plan = Plan(goal="g", subtasks=[
        SubTask(id="feat", kind="edit", role="impl", prompt="add a feature")])
    synth = FakeProvider(responses=[LLMResponse(text="s")])
    result = run_orchestrator(_cfg(), "g", plan=plan, root=tmp_path, synth_provider=synth)

    edit = result.subresults[0]
    assert edit.ok and "feature.py" in edit.diff and "def feature" in edit.diff
    # main checkout untouched; worktree cleaned up
    assert not (tmp_path / "feature.py").exists()
    assert seen_roots and not seen_roots[0].exists()
    assert result.edits and result.edits[0].id == "feat"


def test_run_orchestrator_parallel_edits_no_collision(monkeypatch, tmp_path) -> None:
    _init_repo(tmp_path)

    def fake_run(config, prompt, *, root, **kwargs):
        name = prompt.strip().split()[-1]
        (Path(root) / f"{name}.py").write_text(f"# {name}\n", encoding="utf-8")
        return CodeRunResult(success=True, output=f"did {name}", iterations=1)

    monkeypatch.setattr(code_mode, "run_code_agent", fake_run)
    plan = Plan(goal="g", subtasks=[
        SubTask(id="one", kind="edit", prompt="make alpha"),
        SubTask(id="two", kind="edit", prompt="make beta"),
    ])
    synth = FakeProvider(responses=[LLMResponse(text="s")])
    result = run_orchestrator(_cfg(), "g", plan=plan, root=tmp_path, synth_provider=synth,
                              max_workers=2)
    by_id = {r.id: r for r in result.subresults}
    # each isolated diff carries only its own file — no cross-contamination
    assert "alpha.py" in by_id["one"].diff and "beta.py" not in by_id["one"].diff
    assert "beta.py" in by_id["two"].diff and "alpha.py" not in by_id["two"].diff


def test_run_orchestrator_edit_without_git_fails_gracefully(monkeypatch, tmp_path) -> None:
    # tmp_path is NOT a git repo -> the edit subtask reports it, no crash
    def fake_run(config, prompt, **kwargs):
        raise AssertionError("run_code_agent should not be called without a worktree")

    monkeypatch.setattr(code_mode, "run_code_agent", fake_run)
    plan = Plan(goal="g", subtasks=[SubTask(id="e", kind="edit", prompt="p")])
    synth = FakeProvider(responses=[LLMResponse(text="s")])
    result = run_orchestrator(_cfg(), "g", plan=plan, root=tmp_path, synth_provider=synth)
    assert not result.subresults[0].ok
    assert "cannot isolate" in (result.subresults[0].error or "")


# ---------- synthesize (injected judge) ----------

def test_synthesize_single_research_skips_judge() -> None:
    def boom(_cfg):
        raise AssertionError("judge should not run for a single non-diff result")

    # build_single_provider would raise if called -> proves it is skipped
    import ronin_cli.runner as runner
    orig = runner.build_single_provider
    runner.build_single_provider = boom
    try:
        results = [orchestrator.SubResult("a", "r", "anthropic:x", "research",
                                          ok=True, output="the answer")]
        assert synthesize(_cfg(), "g", results) == "the answer"
    finally:
        runner.build_single_provider = orig


def test_synthesize_no_results_message() -> None:
    results = [orchestrator.SubResult("a", "r", "x", "research", ok=False, error="boom")]
    assert "no subagent produced a result" in synthesize(_cfg(), "g", results)


# ---------- delegate tool ----------

def test_build_orchestrate_tool_runs_and_formats(monkeypatch, tmp_path) -> None:
    def fake_run(config, prompt, **kwargs):
        return CodeRunResult(success=True, output="explored", iterations=1)

    monkeypatch.setattr(code_mode, "run_code_agent", fake_run)
    # skip the planner by faking make_plan via a canned plan path: monkeypatch it
    plan = Plan(goal="g", subtasks=[SubTask(id="x", kind="research", role="r", prompt="p")])
    monkeypatch.setattr(orchestrator, "make_plan", lambda *a, **k: plan)
    monkeypatch.setattr(orchestrator, "synthesize", lambda *a, **k: "SYNTH")

    tool = orchestrator.build_orchestrate_tool(_cfg(), tmp_path)
    assert tool.name == "orchestrate"
    out = tool.handler(goal="do a thing")
    assert "Orchestrated 1 subagent(s)" in out
    assert "`x`" in out and "SYNTH" in out
