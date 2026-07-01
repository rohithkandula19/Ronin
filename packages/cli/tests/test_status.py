"""Tests for the Wave 2 status line: cost/free badge, git awareness, mode chips."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ronin_cli.config import RoninConfig
from ronin_cli.status import (
    GitState,
    active_modes,
    cost_badge,
    git_state,
    mode_chip,
    parse_porcelain_branch,
    status_markup,
    status_text,
)


# --- cost / free badge -------------------------------------------------------

def test_badge_free_for_free_tier_providers() -> None:
    for prov in ("gemini", "cerebras", "groq"):
        label, _ = cost_badge(RoninConfig(provider=prov))
        assert label == "FREE", prov


def test_badge_free_for_openrouter_free_model() -> None:
    # default openrouter model is a `:free` one
    assert cost_badge(RoninConfig(provider="openrouter"))[0] == "FREE"


def test_badge_paid_for_paid_provider() -> None:
    assert cost_badge(RoninConfig(provider="anthropic"))[0] == "PAID"


def test_badge_local_for_ollama_and_offline() -> None:
    assert cost_badge(RoninConfig(provider="ollama"))[0] == "LOCAL"
    assert cost_badge(RoninConfig(provider="local"))[0] == "LOCAL"
    # offline forces LOCAL regardless of the named provider
    assert cost_badge(RoninConfig(provider="anthropic", offline=True))[0] == "LOCAL"


def test_badge_unknown_for_custom_endpoint() -> None:
    cfg = RoninConfig(provider="custom", model="some-private-model", base_url="http://x")
    assert cost_badge(cfg)[0] == "UNKNOWN"


def test_badge_known_model_on_custom_is_classified() -> None:
    # a recognizable model name (e.g. a :free id) is classified even on custom
    cfg = RoninConfig(provider="custom", model="qwen/qwen3-coder:free", base_url="http://x")
    assert cost_badge(cfg)[0] == "FREE"


# --- git awareness -----------------------------------------------------------

def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True,
                   capture_output=True, text=True)


def test_git_state_detects_branch_and_dirty(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t.t")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "checkout", "-q", "-b", "feature-x")
    (tmp_path / "a.txt").write_text("hi", encoding="utf-8")
    _git(tmp_path, "add", "a.txt")
    _git(tmp_path, "commit", "-q", "-m", "init")
    st = git_state(tmp_path)
    assert st.repo and st.branch == "feature-x"
    assert st.dirty is False and st.label() == "feature-x"
    # make the tree dirty
    (tmp_path / "a.txt").write_text("changed", encoding="utf-8")
    st2 = git_state(tmp_path)
    assert st2.dirty is True and st2.changes >= 1
    assert st2.label() == "feature-x*"


def test_git_state_outside_repo_does_not_crash(tmp_path: Path) -> None:
    st = git_state(tmp_path)  # plain temp dir, no git
    assert st == GitState()
    assert st.repo is False
    assert st.label() == ""  # nothing to render


def test_parse_porcelain_ahead_behind() -> None:
    st = parse_porcelain_branch("## main...origin/main [ahead 2, behind 3]\n M file.py\n")
    assert st.branch == "main" and st.ahead == 2 and st.behind == 3
    assert st.dirty is True
    assert st.label() == "main* ↑2↓3"


def test_parse_porcelain_clean_no_upstream() -> None:
    st = parse_porcelain_branch("## main\n")
    assert st.branch == "main" and not st.dirty and st.ahead == 0
    assert st.label() == "main"


def test_parse_porcelain_detached_and_no_commits() -> None:
    assert parse_porcelain_branch("## HEAD (no branch)\n").branch == "(detached)"
    assert parse_porcelain_branch("## No commits yet on main\n").branch == "main"


# --- mode chips --------------------------------------------------------------

def test_mode_chip_renders_each_known_mode_distinctly() -> None:
    names = ["normal", "plan", "auto-accept", "offline", "free", "scout", "review"]
    rendered = {}
    for n in names:
        label, hex_ = mode_chip(n)
        assert label == f"◈ {n}"
        assert hex_.startswith("#")
        rendered[n] = hex_
    # every mode has its own colour (no two collide)
    assert len(set(rendered.values())) == len(names)


def test_active_modes_reflects_config() -> None:
    base = RoninConfig(provider="anthropic")  # paid, no special modes
    assert active_modes(base, "normal") == ["normal"]
    # free provider adds the free chip
    assert "free" in active_modes(RoninConfig(provider="cerebras"))
    # offline + scout (routing) + review (verify)
    cfg = RoninConfig(provider="anthropic", offline=True, verify=True,
                      route_fast="groq:llama", route_strong="cerebras:gpt-oss-120b")
    modes = active_modes(cfg, "auto-accept")
    assert modes[0] == "auto-accept"
    assert {"offline", "scout", "review"} <= set(modes)


# --- status line rendering ---------------------------------------------------

def test_status_text_is_plain_and_has_segments() -> None:
    cfg = RoninConfig(provider="gemini")
    g = GitState(repo=True, branch="main", dirty=True, changes=2)
    text = status_text(cfg, ".", edit_mode="normal", ctx_tokens=12400, git=g)
    assert "[" not in text  # plain, no rich markup for the prompt_toolkit toolbar
    assert "FREE" in text
    assert f"gemini/{cfg.resolved_model()}" in text
    assert "normal" in text
    assert "main*" in text
    assert "12.4k ctx" in text


def test_status_markup_includes_elapsed_and_badge() -> None:
    cfg = RoninConfig(provider="anthropic")
    g = GitState(repo=True, branch="dev", dirty=False)
    out = status_markup(cfg, ".", edit_mode="plan", elapsed=18.0, git=g, ctx_tokens=3200)
    assert "PAID" in out and "18s" in out
    assert "dev" in out and "🐼 ronin" in out


def test_status_omits_git_segment_outside_repo() -> None:
    cfg = RoninConfig(provider="groq")
    text = status_text(cfg, ".", git=GitState())  # not a repo
    # no trailing branch noise; still shows badge + model
    assert "FREE" in text
    assert text.count(" · ") >= 1


# --- chip strip (Wave 3) -----------------------------------------------------

from ronin_cli.status import chip_strip, gate_label


def test_chip_strip_full_row() -> None:
    cfg = RoninConfig(provider="cerebras")
    g = GitState(repo=True, branch="main", dirty=True, changes=1)
    strip = chip_strip(cfg, ".", edit_mode="normal", width=120, git=g)
    assert "[FREE]" in strip
    assert f"[cerebras:{cfg.resolved_model()}]" in strip
    assert "[normal]" in strip
    assert "[main*]" in strip
    assert "[write-gated]" in strip


def test_chip_strip_shows_role_chip() -> None:
    cfg = RoninConfig(provider="groq")
    strip = chip_strip(cfg, ".", role="debugger", width=120, git=GitState())
    assert "[role:debugger]" in strip


def test_chip_strip_auto_accept_drops_write_gated() -> None:
    cfg = RoninConfig(provider="anthropic")
    strip = chip_strip(cfg, ".", edit_mode="auto-accept", width=120, git=GitState())
    assert "[auto-accept]" in strip
    assert "write-gated" not in strip  # the mode chip already conveys it


def test_chip_strip_read_only_role_shows_read_only_gate() -> None:
    cfg = RoninConfig(provider="groq")
    strip = chip_strip(cfg, ".", role="reviewer", width=120, git=GitState())
    assert "[read-only]" in strip


def test_chip_strip_degrades_in_narrow_terminal() -> None:
    cfg = RoninConfig(provider="cerebras")
    g = GitState(repo=True, branch="feature/very-long-branch-name", dirty=True)
    narrow = chip_strip(cfg, ".", edit_mode="normal", role="debugger", width=32, git=g)
    # fits the width once low-priority chips shed ([FREE] [normal] [write-gated])
    assert len(narrow) <= 32
    # the safety + cost signals survive
    assert "[FREE]" in narrow
    assert "[normal]" in narrow
    # lowest-priority chips (role / branch) are the first to be dropped
    assert "[role:debugger]" not in narrow


def test_chip_strip_outside_git_has_no_branch() -> None:
    cfg = RoninConfig(provider="groq")
    strip = chip_strip(cfg, ".", width=120, git=GitState())  # not a repo
    assert "[FREE]" in strip
    # no branch chip, no crash
    assert "*" not in strip


def test_gate_label_semantics() -> None:
    assert gate_label("normal") == "write-gated"
    assert gate_label("plan") == "read-only"
    assert gate_label("auto-accept") is None
    assert gate_label("normal", full_access=True) is None
    assert gate_label("normal", read_only=True) == "read-only"


# --- Phase 6: god-mode + destructive-floor safety chips ----------------------

def test_god_mode_chip_and_destructive_floor() -> None:
    cfg = RoninConfig(provider="anthropic")
    strip = chip_strip(cfg, ".", edit_mode="normal", width=120, git=GitState(),
                       full_access=True)
    assert "[god-mode]" in strip
    assert "[DESTRUCTIVE FLOOR ACTIVE]" in strip
    assert "[PAID]" in strip


def test_god_mode_safety_chips_survive_narrow() -> None:
    cfg = RoninConfig(provider="anthropic")
    g = GitState(repo=True, branch="feature/really-long-branch-name", dirty=True)
    narrow = chip_strip(cfg, ".", edit_mode="normal", role="implementer",
                        width=44, git=g, full_access=True)
    assert len(narrow) <= 44
    # cost + god-mode + destructive floor never disappear
    assert "[PAID]" in narrow
    assert "[god-mode]" in narrow
    assert "[DESTRUCTIVE FLOOR ACTIVE]" in narrow
    # low-priority chips (role / branch) shed first
    assert "[role:implementer]" not in narrow


def test_normal_mode_has_no_god_chips() -> None:
    cfg = RoninConfig(provider="cerebras")
    strip = chip_strip(cfg, ".", edit_mode="normal", width=120, git=GitState())
    assert "god-mode" not in strip
    assert "DESTRUCTIVE FLOOR" not in strip
    assert "[write-gated]" in strip  # normal safety chip


def test_safety_chips_survive_extreme_narrow() -> None:
    # even absurdly narrow, cost + safety chips are never dropped (may overflow,
    # but they must be present — trust over prettiness)
    cfg = RoninConfig(provider="anthropic")
    strip = chip_strip(cfg, ".", edit_mode="normal", role="implementer",
                       width=10, git=GitState(repo=True, branch="main", dirty=True),
                       full_access=True)
    assert "[PAID]" in strip and "[god-mode]" in strip
    assert "[DESTRUCTIVE FLOOR ACTIVE]" in strip
