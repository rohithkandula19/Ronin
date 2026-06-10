"""Regression: the live renderer must never crash on bracketed tool I/O.

A command like `grep "[/INST]"` or a file containing `[/bold]` would otherwise
raise rich.errors.MarkupError mid-turn — and the tool_call announcement fires
BEFORE the approval gate, so it crashed the turn before the prompt even showed.
"""
from __future__ import annotations

import io

from rich.console import Console

from ronin_agent_patterns import Step
from ronin_cli.streaming import LiveRenderer


def _render(step: Step) -> str:
    buf = io.StringIO()
    r = LiveRenderer(Console(file=buf, force_terminal=False, width=100))
    r.on_step(step)         # must not raise
    return buf.getvalue()


def test_tool_call_with_bracket_command_does_not_crash() -> None:
    out = _render(Step(kind="tool_call",
                       content={"name": "run_command", "input": {"command": 'grep "[/INST]" .'}}))
    assert "INST" in out


def test_tool_result_with_brackets_does_not_crash() -> None:
    _render(Step(kind="tool_result",
                 content={"name": "read_file", "result": "line [/bold] with a tag", "is_error": False}))
    _render(Step(kind="tool_result",
                 content={"name": "run_command", "result": "err [/red] here", "is_error": True}))


def test_error_step_with_close_tag_does_not_crash() -> None:
    # a free-text gate-denial reason carrying a closing tag
    _render(Step(kind="error", content="tool denied: use the [id] route not [/slug]"))


def test_plan_and_reflection_with_brackets_do_not_crash() -> None:
    _render(Step(kind="plan", content="step 1: edit [id].tsx"))
    _render(Step(kind="reflection", content="checked [/done]"))
