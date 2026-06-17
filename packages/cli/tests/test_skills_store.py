"""Tests for skills — reusable, parameterized procedures the agent can follow.

Fast and offline: the store is local JSON files; the one agent-loop test uses
the FakeProvider, no network and no provider key.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from ronin_agent_patterns import FakeProvider, LLMResponse, ToolCall
from ronin_cli import skills_store
from ronin_cli.main import app

runner = CliRunner()


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


# ---------- pure helpers ----------

def test_normalize_and_validate_name() -> None:
    assert skills_store.normalize_name("Ship Release") == "ship-release"
    assert skills_store.normalize_name("  cut__release!! ") == "cut-release"
    assert skills_store.is_valid_name("ship-release") is True
    assert skills_store.is_valid_name("") is False
    assert skills_store.is_valid_name("-bad") is False


def test_extract_params_first_seen_order_deduped() -> None:
    steps = "1. bump {file} to {version}\n2. tag v{version}\n3. push {file}"
    assert skills_store.extract_params(steps) == ["file", "version"]
    assert skills_store.extract_params("no slots here") == []


def test_parse_kv_keeps_values_with_equals() -> None:
    kv = skills_store.parse_kv(["repo=ronin", "msg=a=b=c", "ignored", "=novalue"])
    assert kv == {"repo": "ronin", "msg": "a=b=c"}


# ---------- persistence: save / load / list / forget ----------

def test_save_persists_and_round_trips(home: Path) -> None:
    saved = skills_store.save_skill(
        "cut-release", "Cut a release", "1. bump {file}\n2. tag v{version}")
    assert saved is not None
    assert saved.params == ["file", "version"]

    # a brand-new process (fresh load from disk) still has it
    loaded = skills_store.load_skill("cut-release")
    assert loaded is not None
    assert loaded.description == "Cut a release"
    assert loaded.steps.startswith("1. bump {file}")
    assert loaded.params == ["file", "version"]


def test_save_normalizes_name_and_rejects_empty(home: Path) -> None:
    saved = skills_store.save_skill("Ship It", "", "do the thing")
    assert saved is not None and saved.name == "ship-it"
    assert skills_store.load_skill("ship it") is not None  # normalized lookup
    assert skills_store.save_skill("ok", "", "   ") is None  # empty steps rejected
    assert skills_store.save_skill("", "", "steps") is None  # invalid name rejected


def test_list_newest_first_and_forget(home: Path) -> None:
    skills_store.save_skill("a", "", "first {x}")
    skills_store.save_skill("b", "", "second {y}")
    names = [s.name for s in skills_store.list_skills()]
    assert names == ["b", "a"]                    # newest first
    assert skills_store.forget_skill("a") is True
    assert skills_store.forget_skill("a") is False  # already gone
    assert [s.name for s in skills_store.list_skills()] == ["b"]


def test_overwrite_guard(home: Path) -> None:
    skills_store.save_skill("dup", "", "v1 {x}")
    assert skills_store.save_skill("dup", "", "v2 {x}", overwrite=False) is None
    assert skills_store.load_skill("dup").steps == "v1 {x}"
    assert skills_store.save_skill("dup", "", "v2 {x}", overwrite=True) is not None
    assert skills_store.load_skill("dup").steps == "v2 {x}"


# ---------- rendering (the parameterized part) ----------

def test_render_fills_slots_and_reports_missing(home: Path) -> None:
    skill = skills_store.Skill(
        name="rel", description="", steps="bump {file} to v{version}; push {file}",
        params=["file", "version"])
    r = skills_store.render_skill(skill, {"file": "pyproject.toml", "version": "1.2"})
    assert r.text == "bump pyproject.toml to v1.2; push pyproject.toml"
    assert r.missing == []
    assert r.used == {"file": "pyproject.toml", "version": "1.2"}

    r2 = skills_store.render_skill(skill, {"file": "x"})
    assert "{version}" in r2.text          # left intact, not blanked
    assert r2.missing == ["version"]


# ---------- the agent is OFFERED the skills (injection) ----------

def test_prompt_block_injects_saved_skills(home: Path) -> None:
    assert skills_store.skills_prompt_block() == ""     # nothing yet
    skills_store.save_skill("cut-release", "Cut a release", "tag v{version} of {repo}")
    block = skills_store.skills_prompt_block()
    assert "Skills you can follow" in block
    assert "cut-release" in block
    assert "version" in block and "repo" in block       # params advertised


def test_use_skill_tool_returns_rendered_steps(home: Path) -> None:
    skills_store.save_skill("greet", "say hi", "Hello {name}!")
    tools = {t.name: t for t in skills_store.build_skill_tools()}
    out = tools["use_skill"].handler(name="greet", params={"name": "Rohith"})
    assert "Hello Rohith!" in out
    miss = tools["use_skill"].handler(name="greet", params={})
    assert "{name}" in miss and "Unfilled params" in miss
    assert "No skill named" in tools["use_skill"].handler(name="nope")


def test_save_skill_tool_persists(home: Path) -> None:
    tools = {t.name: t for t in skills_store.build_skill_tools()}
    msg = tools["save_skill"].handler(
        name="deploy", description="ship", steps="1. build {target}\n2. ship")
    assert "Saved skill 'deploy'" in msg and "target" in msg
    assert skills_store.load_skill("deploy") is not None


# ---------- CLI ----------

def test_cli_new_list_show_run_forget(home: Path) -> None:
    r = runner.invoke(app, ["skills", "new", "Cut Release",
                            "--steps", "1. bump {file}\n2. tag v{version}",
                            "--desc", "release flow"])
    assert r.exit_code == 0 and "saved skill" in r.stdout and "cut-release" in r.stdout

    r = runner.invoke(app, ["skills", "list"])
    assert "cut-release" in r.stdout and "release flow" in r.stdout

    r = runner.invoke(app, ["skills", "show", "cut-release"])
    assert "{file}" in r.stdout and "{version}" in r.stdout

    # run with all params -> filled, exit 0
    r = runner.invoke(app, ["skills", "run", "cut-release", "file=pyproject.toml", "version=2.0"])
    assert r.exit_code == 0 and "pyproject.toml" in r.stdout and "v2.0" in r.stdout

    # run missing a param -> reported, exit 1
    r = runner.invoke(app, ["skills", "run", "cut-release", "file=x"])
    assert r.exit_code == 1 and "unfilled params" in r.stdout and "version" in r.stdout

    r = runner.invoke(app, ["skills", "forget", "cut-release"])
    assert r.exit_code == 0 and "forgot skill" in r.stdout
    r = runner.invoke(app, ["skills", "list"])
    assert "no skills yet" in r.stdout


def test_cli_new_overwrite_guard(home: Path) -> None:
    runner.invoke(app, ["skills", "new", "dup", "--steps", "v1"])
    r = runner.invoke(app, ["skills", "new", "dup", "--steps", "v2"])
    assert r.exit_code == 1 and "already exists" in r.stdout
    r = runner.invoke(app, ["skills", "new", "dup", "--steps", "v2", "--force"])
    assert r.exit_code == 0
    from ronin_cli.skills_store import load_skill
    assert load_skill("dup").steps == "v2"


def test_cli_new_from_session(home: Path) -> None:
    # archive a session, then learn a skill from its transcript
    from ronin_cli import sessions
    sid = "20260617-101010-abc123"
    sessions.save_session(home, ["USER: fix the auth bug", "ASSISTANT: edited login.py"],
                          session_id=sid)
    r = runner.invoke(app, ["skills", "new", "fix-auth", "--from-session", sid,
                            "--desc", "auth playbook"])
    assert r.exit_code == 0
    from ronin_cli.skills_store import load_skill
    sk = load_skill("fix-auth")
    assert sk is not None and "login.py" in sk.steps


def test_agent_loop_offers_skill_and_can_use_it(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end with the mock provider: a saved skill is injected into the
    system prompt AND the agent can call `use_skill` to fetch its steps."""
    import io

    from rich.console import Console

    from ronin_cli import code_mode
    from ronin_cli.config import RoninConfig

    skills_store.save_skill("greet-user", "greets", "Say hello to {name}.")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    config = RoninConfig(provider="anthropic", demo_mode=True)
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=100)
    it = iter(["greet Rohith using the skill", "/q"])
    console.input = lambda *a, **k: next(it)  # type: ignore[assignment]

    provider = FakeProvider(responses=[
        LLMResponse(text="Using the skill.", tool_calls=[ToolCall(
            id="t1", name="use_skill",
            arguments={"name": "greet-user", "params": {"name": "Rohith"}})],
            stop_reason="tool_use", usage={}),
        LLMResponse(text="Hello Rohith!", stop_reason="end_turn", usage={}),
    ])
    from unittest.mock import patch
    with patch("ronin_cli.code_mode.build_provider", return_value=provider):
        code_mode.run_unified_session(config, root=home, console=console)

    # the skill was advertised to the model in its system prompt...
    assert any("Skills you can follow" in c["system"] for c in provider.calls)
    assert any("greet-user" in c["system"] for c in provider.calls)
    # ...and use_skill was a registered tool the agent could call.
    assert any("use_skill" in c["tool_names"] for c in provider.calls)
