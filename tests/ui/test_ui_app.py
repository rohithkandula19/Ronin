"""The app as an adapter: everything testable without a terminal, tested here.

What is *not* covered here is stated plainly rather than faked: the widget tree,
the CSS layout, and Textual's own key dispatch need Textual installed and a
terminal (or its headless pilot). Those are the lines this suite cannot reach, so
every decision was moved out of them — into ``reduce``, ``render`` and the two
functions below — and the remainder is assignment statements.
"""

from __future__ import annotations

import ast
import inspect
import os
import subprocess
import sys
from pathlib import Path

import pytest
from ui_harness import APPROVAL, happy_turn, stream

from ronin.core.types import Mode
from ronin.ui import app as app_module
from ronin.ui.app import (
    APP_CSS,
    APPROVAL_ID,
    ERRORS_ID,
    STATUS_ID,
    TEXTUAL_MISSING,
    TODOS_ID,
    TOOLS_ID,
    TRANSCRIPT_ID,
    KeyController,
    Session,
    initial_state,
    panels_for,
    run_app,
    textual_available,
)
from ronin.ui.reduce import EscapeAction, reduce_all


def test_nothing_at_module_scope_imports_textual() -> None:
    # The whole point of the lazy import. Checked structurally rather than by
    # string search, so a future `import textual` at the top cannot slip past.
    tree = ast.parse(inspect.getsource(app_module))
    imported: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert not any(name.startswith("textual") for name in imported)


def test_importing_the_package_does_not_drag_textual_in() -> None:
    # In a fresh interpreter, because this one may have imported Textual for the
    # pilot tests. Local subprocess, no network: `ronin.ui` must be importable and
    # usable on an install that has no TUI extra at all.
    source = str(Path(app_module.__file__).resolve().parents[2])
    env = {**os.environ, "PYTHONPATH": source}
    proof = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, ronin.ui; print('textual' in sys.modules)",
        ],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    assert proof.stdout.strip() == "False"


async def test_absence_of_the_extra_is_reported_as_a_fact_not_a_crash(
    capsys: pytest.CaptureFixture[str],
) -> None:
    if textual_available():  # pragma: no cover - depends on the install
        pytest.skip("textual is installed here, so the missing-extra path is unreachable")
    code = await run_app(Session(events=stream(())))
    assert code == 1
    assert TEXTUAL_MISSING in capsys.readouterr().out


async def test_the_missing_extra_path_is_covered_whatever_is_installed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The test above skips when Textual happens to be installed; this one pins the
    # bare-install behaviour either way, because that is the path a first-time user
    # hits and it must not be the untested one.
    monkeypatch.setattr(app_module, "textual_available", lambda: False)
    code = await run_app(Session(events=stream(())))
    assert code == 1
    assert TEXTUAL_MISSING in capsys.readouterr().out


def test_the_missing_extra_message_says_what_still_works() -> None:
    assert "tui" in TEXTUAL_MISSING
    assert "--output-format=stream-json" in TEXTUAL_MISSING
    assert "ronin.ui.demo" in TEXTUAL_MISSING


def test_every_css_region_matches_a_widget_id() -> None:
    for region in (TRANSCRIPT_ID, TOOLS_ID, TODOS_ID, APPROVAL_ID, ERRORS_ID, STATUS_ID):
        assert f"#{region}" in APP_CSS


def test_the_initial_state_carries_the_injected_status_facts() -> None:
    session = Session(
        events=stream(()),
        model="claude-sonnet",
        cwd="/repo",
        branch="main",
        context_used=0.25,
        mode=Mode.PLAN,
    )
    state = initial_state(session)
    assert state.model == "claude-sonnet"
    assert state.cwd == "/repo"
    assert state.branch == "main"
    assert state.context_used == 0.25
    assert state.mode is Mode.PLAN


def test_the_panels_the_app_paints_come_from_the_pure_renderer() -> None:
    state = reduce_all(happy_turn()).with_status(model="m", branch="main")
    panels = panels_for(state)
    assert "Reading src/main.py now." in panels.transcript
    assert "● Read(" in panels.tools
    assert "main" in panels.status


def test_the_default_app_styles_are_console_markup_not_ansi() -> None:
    panels = panels_for(reduce_all((APPROVAL,)))
    assert "[bold red]" in panels.approval
    assert "\x1b[" not in panels.approval


def test_the_key_controller_cycles_modes_without_a_terminal() -> None:
    keys = KeyController(mode=Mode.ASK)
    assert keys.cycle_mode() is Mode.AUTO_EDIT
    assert keys.cycle_mode() is Mode.PLAN
    assert keys.cycle_mode() is Mode.ASK
    assert keys.mode is Mode.ASK


def test_the_key_controller_double_press_uses_an_injected_clock() -> None:
    ticks = iter([0.0, 0.1, 5.0])
    keys = KeyController(clock=lambda: next(ticks))
    assert keys.press_escape() is EscapeAction.INTERRUPT
    assert keys.press_escape() is EscapeAction.REWIND
    assert keys.press_escape() is EscapeAction.INTERRUPT


def test_the_session_is_data_the_orchestrator_injects() -> None:
    calls: list[str] = []
    session = Session(
        events=stream(()),
        on_interrupt=lambda: calls.append("interrupt"),
        on_rewind=lambda index: calls.append(f"rewind:{index}"),
        on_mode_change=lambda mode: calls.append(f"mode:{mode.value}"),
        on_approval=lambda request: calls.append(f"approval:{request.name}"),
    )
    assert session.on_interrupt is not None
    session.on_interrupt()
    assert session.on_rewind is not None
    session.on_rewind(3)
    assert session.on_mode_change is not None
    session.on_mode_change(Mode.PLAN)
    assert session.on_approval is not None
    session.on_approval(APPROVAL)
    assert calls == ["interrupt", "rewind:3", "mode:plan", "approval:Bash"]


def test_the_app_never_answers_an_approval_itself() -> None:
    # There is no ApprovalDecision anywhere in app.py: the UI shows the request and
    # hands it to the caller. A UI that could construct a decision could approve.
    source = inspect.getsource(app_module)
    assert "ApprovalDecision" not in source
    assert "approved=True" not in source
