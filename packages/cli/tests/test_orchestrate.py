"""Tests for the CLI orchestrator bridge (ronin_cli.orchestrate).

The roster parsing and provider/spec resolution are pure. The end-to-end run is
driven entirely by ``FakeProvider`` (no network, no keys): we patch
``provider_for_spec`` so each role gets a deterministic mock provider, proving
the bridge decomposes a goal, runs each sub-agent on its ASSIGNED provider, and
returns the synthesized result — all offline.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from typer.testing import CliRunner

from ronin_agent_patterns import FakeProvider, LLMResponse

from ronin_cli import orchestrate
from ronin_cli.config import RoninConfig

runner = CliRunner()


def _cfg() -> RoninConfig:
    return RoninConfig(provider="anthropic", anthropic_api_key="sk-x", model="claude-sonnet-4-6")


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


# ---------- pure roster parsing ----------


def test_parse_roster_maps_roles_to_specs() -> None:
    out = orchestrate.parse_roster(
        "researcher=anthropic,implementer=cerebras:gpt-oss-120b,reviewer=gemini")
    assert out == {
        "researcher": "anthropic",
        "implementer": "cerebras:gpt-oss-120b",
        "reviewer": "gemini",
    }


def test_parse_roster_empty_is_empty() -> None:
    assert orchestrate.parse_roster(None) == {}
    assert orchestrate.parse_roster("") == {}


def test_parse_roster_blank_provider_means_base() -> None:
    assert orchestrate.parse_roster("researcher=") == {"researcher": None}


# ---------- provider/label resolution (pure, no network) ----------


def test_role_label_uses_base_when_unspecified() -> None:
    assert orchestrate.role_label(_cfg(), None) == "anthropic:claude-sonnet-4-6"


def test_role_label_honours_spec() -> None:
    label = orchestrate.role_label(_cfg(), "gemini:gemini-2.0-flash")
    assert label == "gemini:gemini-2.0-flash"


def test_build_subagents_assigns_distinct_providers(monkeypatch) -> None:
    """Each role gets the provider its spec resolves to — the provider-agnostic core."""
    seen: list[str | None] = []

    def fake_provider(base, spec):
        seen.append(spec)
        return FakeProvider(model=str(spec or "base"))

    monkeypatch.setattr(orchestrate, "provider_for_spec", fake_provider)
    subs = orchestrate.build_subagents(
        _cfg(),
        {"researcher": "anthropic", "implementer": "cerebras", "reviewer": "gemini",
         "tester": "groq"},
    )
    by_role = {s.role: s for s in subs}
    assert by_role["researcher"].provider.model == "anthropic"
    assert by_role["implementer"].provider.model == "cerebras"
    assert by_role["reviewer"].provider.model == "gemini"
    assert by_role["tester"].provider.model == "groq"


def test_default_roster_includes_the_four_builtin_roles() -> None:
    """The built-in specialist roster is researcher/implementer/reviewer/tester."""
    assert set(orchestrate.DEFAULT_ROLES) == {
        "researcher", "implementer", "reviewer", "tester",
    }
    # every role also carries a human description for the planner's roster block
    assert set(orchestrate.DEFAULT_DESCRIPTIONS) == set(orchestrate.DEFAULT_ROLES)


def test_tester_role_can_run_commands_in_readonly(tmp_path) -> None:
    """The tester gets run_command even in read-only mode so it can execute an
    existing suite, while researcher/reviewer stay strictly read-only."""
    _init_repo(tmp_path)
    tools_for_role = orchestrate._tools_for_roles(
        _cfg(), tmp_path, read_only=True, mutate_root=tmp_path)
    tester_tools = {t.name for t in tools_for_role["tester"]}
    researcher_tools = {t.name for t in tools_for_role["researcher"]}
    assert "run_command" in tester_tools
    assert "run_command" not in researcher_tools
    # the tester must not get mutating tools in read-only mode
    assert "write_file" not in tester_tools
    assert "edit_file" not in tester_tools


# ---------- end-to-end run, fully offline ----------


def test_run_orchestrate_decomposes_and_runs_on_assigned_providers(monkeypatch) -> None:
    # one mock provider per role + the planner/synthesizer (spec=None)
    providers: dict[str | None, FakeProvider] = {
        None: FakeProvider(model="base", responses=[
            # planner call -> a 2-subtask plan
            LLMResponse(text=(
                "<plan>"
                '{"goal": "g", "subtasks": ['
                '{"id": "find", "description": "find it", "assignee": "researcher", "depends_on": []},'
                '{"id": "review", "description": "review it", "assignee": "reviewer", "depends_on": ["find"]}'
                "]}"
                "</plan>")),
            # synthesizer call
            LLMResponse(text="Done: found and reviewed."),
        ]),
        "researcher=anthropic": FakeProvider(model="anthropic", responses=[
            LLMResponse(text="found in foo.py"),
        ]),
        "reviewer=gemini": FakeProvider(model="gemini", responses=[
            LLMResponse(text="looks good"),
        ]),
    }

    # map a role's resolved spec back to its provider; researcher/reviewer carry
    # specs, implementer + planner resolve to base.
    def fake_provider(base, spec):
        if spec == "anthropic":
            return providers["researcher=anthropic"]
        if spec == "gemini":
            return providers["reviewer=gemini"]
        return providers[None]

    monkeypatch.setattr(orchestrate, "provider_for_spec", fake_provider)
    # keep sub-agents tool-free so the FakeProvider's text response ends the loop
    monkeypatch.setattr(orchestrate, "_tools_for_roles", lambda *a, **k: {})

    outcome = orchestrate.run_orchestrate(
        _cfg(), "do the thing",
        roster_spec="researcher=anthropic,reviewer=gemini",
        read_only=True,
    )

    # decomposed into the planned subtasks
    assert [st["id"] for st in outcome.plan_subtasks] == ["find", "review"]
    # each sub-agent ran on its assigned provider
    assert len(providers["researcher=anthropic"].calls) == 1
    assert len(providers["reviewer=gemini"].calls) == 1
    # results came back + synthesized
    assert outcome.success
    assert "found and reviewed" in outcome.output.lower()
    by_id = {r["subtask_id"]: r for r in outcome.subtask_results}
    assert "foo.py" in by_id["find"]["output"]


def test_run_orchestrate_reports_planning_failure(monkeypatch) -> None:
    base = FakeProvider(model="base", responses=[
        LLMResponse(text="not a plan at all"),  # no <plan> + invalid JSON
    ])
    monkeypatch.setattr(orchestrate, "provider_for_spec", lambda b, s: base)
    monkeypatch.setattr(orchestrate, "_tools_for_roles", lambda *a, **k: {})

    outcome = orchestrate.run_orchestrate(_cfg(), "goal", read_only=True)
    assert outcome.success is False
    assert outcome.plan_subtasks == []
    assert outcome.error


# ---------- write mode: real git-worktree isolation ----------


def test_write_mode_runs_implementer_in_isolated_worktree(monkeypatch, tmp_path) -> None:
    """In --write mode the implementer edits in an isolated worktree: the main
    checkout is never touched and the captured diff contains the new file.

    The implementer's FakeProvider emits a real ``write_file`` tool call, so the
    file is actually created on disk inside the worktree (reusing ronin's code
    tools + the dojo's git_worktree isolation) — not mocked."""
    _init_repo(tmp_path)
    from ronin_agent_patterns import ToolCall

    base_provider = FakeProvider(model="base", responses=[
        LLMResponse(text=(
            "<plan>"
            '{"goal": "g", "subtasks": ['
            '{"id": "build", "description": "create feature.py", "assignee": "implementer", "depends_on": []}'
            "]}"
            "</plan>")),
        LLMResponse(text="Created feature.py."),  # synthesizer
    ])
    impl_provider = FakeProvider(model="impl", responses=[
        LLMResponse(text="", tool_calls=[ToolCall(
            id="w1", name="write_file",
            arguments={"path": "feature.py", "content": "def feature():\n    return 42\n"})],
            stop_reason="tool_use"),
        LLMResponse(text="Wrote feature.py with a feature() function."),
    ])

    def fake_provider(base, spec):
        # implementer is the only role exercised; planner/synth = base
        return impl_provider if spec == "anthropic" else base_provider

    monkeypatch.setattr(orchestrate, "provider_for_spec", fake_provider)

    outcome = orchestrate.run_orchestrate(
        _cfg(), "add a feature module",
        roster_spec="implementer=anthropic", root=tmp_path, read_only=False,
    )

    assert outcome.success
    # the main checkout was NOT mutated
    assert not (tmp_path / "feature.py").exists()
    # the worktree diff captured the new file
    assert "feature.py" in outcome.diff
    assert "def feature" in outcome.diff


def test_write_mode_without_git_falls_back_readonly(monkeypatch, tmp_path) -> None:
    """Outside a git repo, write mode degrades to read-only rather than mutating
    the tree uncontrolled (no worktree available)."""
    seen_read_only: list[bool] = []
    real_tools_for = orchestrate._tools_for_roles

    def spy(base, root, *, read_only, **k):
        seen_read_only.append(read_only)
        return {}

    monkeypatch.setattr(orchestrate, "_tools_for_roles", spy)
    base_provider = FakeProvider(model="base", responses=[
        LLMResponse(text=(
            "<plan>"
            '{"goal": "g", "subtasks": ['
            '{"id": "t", "description": "x", "assignee": "implementer", "depends_on": []}'
            "]}"
            "</plan>")),
        LLMResponse(text="done"),
        LLMResponse(text="synth"),
    ])
    monkeypatch.setattr(orchestrate, "provider_for_spec", lambda b, s: base_provider)

    orchestrate.run_orchestrate(_cfg(), "goal", root=tmp_path, read_only=False)
    # _tools_for_roles was invoked with read_only=True (the safe fallback)
    assert seen_read_only == [True]


# ---------- end-to-end CLI command ----------


def test_cli_orchestrate_offline_runs(monkeypatch) -> None:
    """`ronin orchestrate --offline` runs end-to-end on mock providers and prints
    the synthesized result + the per-subtask status."""
    from ronin_cli.main import app

    base_provider = FakeProvider(model="base", responses=[
        LLMResponse(text=(
            "<plan>"
            '{"goal": "g", "subtasks": ['
            '{"id": "look", "description": "look around", "assignee": "researcher", "depends_on": []}'
            "]}"
            "</plan>")),
        LLMResponse(text="research found the entry point."),  # researcher sub-agent
        LLMResponse(text="Summary: the entry point is main()."),  # synthesizer
    ])
    monkeypatch.setattr(orchestrate, "provider_for_spec", lambda b, s: base_provider)
    monkeypatch.setattr(orchestrate, "_tools_for_roles", lambda *a, **k: {})

    result = runner.invoke(app, ["orchestrate", "explore the repo", "--offline"])
    assert result.exit_code == 0, result.output
    assert "look" in result.output
    assert "entry point" in result.output.lower()
