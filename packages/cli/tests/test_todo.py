from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import patch

import pytest
from rich.console import Console

from ro_claude_kit_agent_patterns import FakeProvider, LLMResponse, ToolCall
from ro_claude_kit_cli.code_mode import run_code_agent
from ro_claude_kit_cli.config import CSKConfig
from ro_claude_kit_cli.todo import TodoStore, build_todo_tool, render_todos


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
    config = CSKConfig(provider="anthropic")
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=100)

    with patch("ro_claude_kit_cli.code_mode.build_provider", return_value=_planning_provider()):
        result = run_code_agent(config, "fix the bug", root=tmp_path, console=console, yolo=True)

    assert result.success
    out = buf.getvalue()
    assert "Plan" in out
    assert "Read target.py" in out and "Apply the fix" in out
