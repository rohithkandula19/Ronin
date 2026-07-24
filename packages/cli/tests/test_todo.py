from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import patch

import pytest
from rich.console import Console

from ronin_agent_patterns import FakeProvider, LLMResponse, ToolCall
from ronin_cli.code_mode import run_code_agent
from ronin_cli.config import RoninConfig
from ronin_cli.todo import (
    IssueDraft,
    TodoStore,
    build_todo_tool,
    context_lines,
    marker_to_issue,
    render_todos,
)


def test_store_replace_cleans_and_validates() -> None:
    store = TodoStore()
    store.replace([
        {"content": "Do A", "status": "completed"},
        {"content": "  ", "status": "pending"},        # blank → dropped
        {"content": "Do B", "status": "bogus"},          # bad status → pending
    ])
    assert store.todos == [
        {"content": "Do A", "status": "completed"},
        {"content": "Do B", "status": "pending"},
    ]
    assert store.summary() == "1/2 done"


def test_tool_updates_store() -> None:
    store = TodoStore()
    tool = build_todo_tool(store)
    msg = tool.handler(todos=[{"content": "Step 1", "status": "in_progress"}])
    assert "updated" in msg.lower()
    assert store.todos == [{"content": "Step 1", "status": "in_progress"}]


def test_render_todos_shows_statuses() -> None:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=80)
    render_todos(console, [
        {"content": "Read the config", "status": "completed"},
        {"content": "Patch the bug", "status": "in_progress"},
        {"content": "Run the tests", "status": "pending"},
    ])
    out = buf.getvalue()
    assert "Plan" in out
    assert "Read the config" in out and "Patch the bug" in out and "Run the tests" in out
    assert "✓" in out and "▶" in out and "☐" in out


def _planning_provider() -> FakeProvider:
    return FakeProvider(responses=[
        LLMResponse(
            text="Let me plan this out.",
            tool_calls=[ToolCall(id="t1", name="update_todos", arguments={"todos": [
                {"content": "Read target.py", "status": "in_progress"},
                {"content": "Apply the fix", "status": "pending"},
            ]})],
            stop_reason="tool_use", usage={"input_tokens": 20, "output_tokens": 10},
        ),
        LLMResponse(text="All done.", stop_reason="end_turn",
                    usage={"input_tokens": 10, "output_tokens": 5}),
    ])


def test_code_agent_renders_plan_checklist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    config = RoninConfig(provider="anthropic")
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=100)

    with patch("ronin_cli.code_mode.build_provider", return_value=_planning_provider()):
        result = run_code_agent(config, "fix the bug", root=tmp_path, console=console, yolo=True)

    assert result.success
    out = buf.getvalue()
    assert "Plan" in out
    assert "Read target.py" in out and "Apply the fix" in out


# --- Wave 3: blocked / failed states + summary -------------------------------

def test_blocked_and_failed_are_valid_statuses() -> None:
    store = TodoStore()
    store.replace([
        {"content": "Read module", "status": "completed"},
        {"content": "Patch bug", "status": "in_progress"},
        {"content": "Run tests", "status": "blocked"},
        {"content": "Deploy", "status": "failed"},
        {"content": "Doc it", "status": "pending"},
    ])
    statuses = [t["status"] for t in store.todos]
    assert statuses == ["completed", "in_progress", "blocked", "failed", "pending"]


def test_summary_counts_blocked_and_failed() -> None:
    store = TodoStore()
    store.replace([
        {"content": "a", "status": "completed"},
        {"content": "b", "status": "blocked"},
        {"content": "c", "status": "failed"},
        {"content": "d", "status": "failed"},
    ])
    s = store.summary()
    assert "1/4 done" in s and "1 blocked" in s and "2 failed" in s


def test_render_shows_all_five_states() -> None:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=80)
    render_todos(console, [
        {"content": "Read auth module", "status": "completed"},
        {"content": "Patch token refresh bug", "status": "in_progress"},
        {"content": "Run focused tests", "status": "blocked"},
        {"content": "Ship", "status": "failed"},
        {"content": "Summarize diff", "status": "pending"},
    ])
    out = buf.getvalue()
    assert "Plan" in out
    for word in ("Read auth module", "Patch token refresh bug", "Run focused tests",
                 "Ship", "Summarize diff"):
        assert word in out
    assert "blocked" in out  # the blocked annotation


def test_render_no_plan_is_silent() -> None:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=80)
    render_todos(console, [])           # no plan → render nothing
    assert buf.getvalue() == ""


def test_unknown_status_still_coerced_to_pending() -> None:
    store = TodoStore()
    store.replace([{"content": "x", "status": "wat"}])
    assert store.todos == [{"content": "x", "status": "pending"}]
# ---- marker -> GitHub-issue draft -----------------------------------------

def test_context_lines_window_and_bounds() -> None:
    lines = [f"l{i}" for i in range(1, 11)]   # l1..l10
    # line 5, default 3 before / 3 after -> l2..l8
    assert context_lines(lines, 5) == ["l2", "l3", "l4", "l5", "l6", "l7", "l8"]
    # clamps at the top
    assert context_lines(lines, 1, before=3, after=1) == ["l1", "l2"]
    # clamps at the bottom
    assert context_lines(lines, 10, before=1, after=3) == ["l9", "l10"]
    # out of range / empty -> []
    assert context_lines(lines, 0) == []
    assert context_lines([], 1) == []


def test_marker_to_issue_shape() -> None:
    d = marker_to_issue("app/db.py", 42, "FIXME", "handle the connection timeout",
                        context=["def connect():", "    # FIXME handle the connection timeout",
                                 "    return pool.get()"])
    assert isinstance(d, IssueDraft)
    assert d.title.startswith("[FIXME]")
    assert "handle the connection timeout" in d.title
    # body carries the file:line reference and a fenced code block
    assert "`app/db.py:42`" in d.body
    assert "```" in d.body and "def connect():" in d.body
    # FIXME maps to a 'fixme' label alongside the 'ronin' tag
    assert d.labels == ["ronin", "fixme"]


def test_marker_to_issue_long_text_is_clipped() -> None:
    d = marker_to_issue("x.py", 1, "TODO", "z" * 300)
    assert len(d.title) <= 120 and d.title.endswith("…")
    assert d.labels == ["ronin", "todo"]


def test_marker_to_issue_no_context_omits_code_block() -> None:
    d = marker_to_issue("x.py", 7, "HACK", "remove this shim")
    assert "```" not in d.body
    assert "`x.py:7`" in d.body and d.labels == ["ronin", "hack"]


# ---- `ronin todo --issues` CLI wiring (gh mocked) -------------------------

def _repo_with_marker(tmp_path: Path) -> None:
    (tmp_path / "svc.py").write_text(
        "def run():\n    pass  # FIXME wire up retries\n    return 1\n", encoding="utf-8")
    (tmp_path / "ok.py").write_text("# TODO add docs\nx = 1\n", encoding="utf-8")


def test_todo_issues_dry_run_does_not_file(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from ronin_cli import gh_helper
    from ronin_cli.main import app

    _repo_with_marker(tmp_path)
    calls: list = []
    with patch.object(gh_helper, "create_issue",
                      side_effect=lambda *a, **k: calls.append(a) or "url"):
        r = CliRunner().invoke(app, ["dev", "todo", "--issues", "--root", str(tmp_path)])
    assert r.exit_code == 0
    assert "dry-run" in r.stdout
    assert "[FIXME]" in r.stdout and "[TODO]" in r.stdout
    assert calls == []                     # nothing filed without --yes


def test_todo_issues_only_filter_and_file(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from ronin_cli import gh_helper
    from ronin_cli.main import app

    _repo_with_marker(tmp_path)
    filed: list = []

    def fake_create(title, body, root=".", labels=None):
        filed.append(title)
        return "https://github.com/o/r/issues/1"

    with patch.object(gh_helper, "create_issue", side_effect=fake_create), \
         patch.object(gh_helper, "gh_available", return_value=True):
        r = CliRunner().invoke(
            app, ["dev", "todo", "--issues", "--only", "FIXME", "--yes", "--root", str(tmp_path)])
    assert r.exit_code == 0
    # only the FIXME marker was filed; the TODO was filtered out
    assert len(filed) == 1 and filed[0].startswith("[FIXME]")


def test_todo_issues_yes_without_gh_exits(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from ronin_cli import gh_helper
    from ronin_cli.main import app

    _repo_with_marker(tmp_path)
    with patch.object(gh_helper, "gh_available", return_value=False):
        r = CliRunner().invoke(app, ["dev", "todo", "--issues", "--yes", "--root", str(tmp_path)])
    assert r.exit_code == 2
    assert "gh" in r.stdout.lower()
