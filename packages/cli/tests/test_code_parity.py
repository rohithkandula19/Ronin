from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from ro_claude_kit_agent_patterns import FakeProvider, LLMResponse
from ro_claude_kit_cli.code_mode import (
    expand_file_mentions,
    load_session,
    run_code_agent,
    save_session,
)
from ro_claude_kit_cli.code_tools import build_code_tools
from ro_claude_kit_cli.config import CSKConfig
from ro_claude_kit_cli.main import _is_code_project


# ---------- @-file mentions ----------

def test_expand_mentions_inlines_file(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("SECRET_MARKER = 1\n", encoding="utf-8")
    out = expand_file_mentions("explain @a.py please", tmp_path)
    assert "Referenced files:" in out and "SECRET_MARKER = 1" in out
    assert "explain @a.py please" in out


def test_expand_mentions_ignores_unknown_and_traversal(tmp_path: Path) -> None:
    out = expand_file_mentions("look at @nope.py and @../etc/passwd", tmp_path)
    assert out == "look at @nope.py and @../etc/passwd"  # nothing inlined


def test_expand_mentions_noop_without_at(tmp_path: Path) -> None:
    assert expand_file_mentions("just a normal task", tmp_path) == "just a normal task"


def test_expand_url_mention_offline_skips_fetch(tmp_path: Path) -> None:
    # offline=True must never touch the network; the @url is left as-is.
    out = expand_file_mentions("read @https://example.com/docs", tmp_path, offline=True)
    assert out == "read @https://example.com/docs"


def test_expand_url_mention_fetches(tmp_path: Path, monkeypatch) -> None:
    # online: the page's text is inlined under "Referenced web pages:".
    import ro_claude_kit_cli.web_tools as web_tools
    monkeypatch.setattr(web_tools, "fetch_url", lambda url, max_chars=4000: "PAGE_BODY_MARKER")
    out = expand_file_mentions("summarize @https://example.com/x", tmp_path)
    assert "Referenced web pages:" in out and "PAGE_BODY_MARKER" in out
    assert "summarize @https://example.com/x" in out


def test_expand_url_mention_skips_failed_fetch(tmp_path: Path, monkeypatch) -> None:
    import ro_claude_kit_cli.web_tools as web_tools
    monkeypatch.setattr(web_tools, "fetch_url", lambda url, max_chars=4000: "ERROR: fetch failed")
    out = expand_file_mentions("read @https://bad.example", tmp_path)
    assert out == "read @https://bad.example"  # error pages are not inlined


# ---------- glob + multi_edit tools ----------

def test_glob_tool(tmp_path: Path) -> None:
    (tmp_path / "x.py").write_text("a")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "y.py").write_text("b")
    (tmp_path / "z.txt").write_text("c")
    tools = {t.name: t for t in build_code_tools(tmp_path)}
    hits = tools["glob"].handler(pattern="**/*.py")
    assert "x.py" in hits and "sub/y.py" in hits and "z.txt" not in hits


def test_multi_edit_applies_all(tmp_path: Path) -> None:
    f = tmp_path / "c.py"
    f.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    tools = {t.name: t for t in build_code_tools(tmp_path)}
    msg = tools["multi_edit"].handler(path="c.py", edits=[
        {"old_string": "alpha", "new_string": "ALPHA"},
        {"old_string": "gamma", "new_string": "GAMMA"},
    ])
    assert "applied 2 edit(s)" in msg
    assert f.read_text() == "ALPHA\nbeta\nGAMMA\n"


def test_multi_edit_all_or_nothing(tmp_path: Path) -> None:
    f = tmp_path / "c.py"
    f.write_text("alpha\nbeta\n", encoding="utf-8")
    tools = {t.name: t for t in build_code_tools(tmp_path)}
    msg = tools["multi_edit"].handler(path="c.py", edits=[
        {"old_string": "alpha", "new_string": "ALPHA"},
        {"old_string": "MISSING", "new_string": "x"},
    ])
    assert msg.startswith("ERROR")
    assert f.read_text() == "alpha\nbeta\n"  # unchanged — validated first


def test_multi_edit_in_sensitive_set() -> None:
    from ro_claude_kit_cli.code_tools import SENSITIVE_TOOLS
    assert "multi_edit" in SENSITIVE_TOOLS


# ---------- _is_code_project ----------

def test_is_code_project(tmp_path: Path) -> None:
    assert _is_code_project(tmp_path) is False
    (tmp_path / "pyproject.toml").write_text("[project]\n")
    assert _is_code_project(tmp_path) is True


def test_is_code_project_git(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    assert _is_code_project(tmp_path) is True


# ---------- session save / load ----------

def test_session_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert load_session(tmp_path) == []
    save_session(tmp_path, ["USER: hi", "ASSISTANT: yo"])
    assert load_session(tmp_path) == ["USER: hi", "ASSISTANT: yo"]


# ---------- plan/read-only filters write tools ----------

def test_read_only_excludes_write_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    config = CSKConfig(provider="anthropic")
    provider = FakeProvider(responses=[LLMResponse(text="plan: do x", stop_reason="end_turn", usage={})])
    with patch("ro_claude_kit_cli.code_mode.build_provider", return_value=provider):
        run_code_agent(config, "plan something", root=tmp_path, console=None, yolo=True, read_only=True)
    names = set(provider.calls[0]["tool_names"])
    assert "write_file" not in names and "edit_file" not in names
    assert "multi_edit" not in names and "run_command" not in names and "generate_image" not in names
    assert "read_file" in names and "glob" in names  # read tools present


def test_normal_mode_includes_write_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    config = CSKConfig(provider="anthropic")
    provider = FakeProvider(responses=[LLMResponse(text="done", stop_reason="end_turn", usage={})])
    with patch("ro_claude_kit_cli.code_mode.build_provider", return_value=provider):
        run_code_agent(config, "do something", root=tmp_path, console=None, yolo=True, read_only=False)
    names = set(provider.calls[0]["tool_names"])
    assert {"write_file", "edit_file", "multi_edit", "run_command"} <= names
