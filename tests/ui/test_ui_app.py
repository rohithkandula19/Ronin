"""The app as an adapter: everything testable without a terminal, tested here.

What is *not* covered here is stated plainly rather than faked: the widget tree,
the CSS layout, and Textual's own key dispatch need Textual installed and a
terminal (or its headless pilot). Those are the lines this suite cannot reach, so
every decision was moved out of them — into ``reduce``, ``render`` and the two
functions below — and the remainder is assignment statements.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from ui_harness import APPROVAL, happy_turn, stream

from ronin.core.types import Event, Mode, TextDelta
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
    multi_turn_events,
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


async def test_the_session_is_data_the_orchestrator_injects() -> None:
    calls: list[str] = []

    async def rewind(index: int) -> str:
        calls.append(f"rewind:{index}")
        return f"rewound to turn {index}"

    session = Session(
        events=stream(()),
        on_interrupt=lambda: calls.append("interrupt"),
        on_rewind=rewind,
        on_mode_change=lambda mode: calls.append(f"mode:{mode.value}"),
        on_approval=lambda request: calls.append(f"approval:{request.name}"),
    )
    assert session.on_interrupt is not None
    session.on_interrupt()
    assert session.on_rewind is not None
    # on_rewind is async and returns the one-line notice the app shows — unlike the
    # other callbacks, which are fire-and-forget.
    assert await session.on_rewind(3) == "rewound to turn 3"
    assert session.on_mode_change is not None
    session.on_mode_change(Mode.PLAN)
    assert session.on_approval is not None
    session.on_approval(APPROVAL)
    assert calls == ["interrupt", "rewind:3", "mode:plan", "approval:Bash"]


def test_the_app_never_answers_an_approval_itself() -> None:
    """The app may *carry* a decision and may not *make* one.

    This used to assert the string ``ApprovalDecision`` appeared nowhere in the module,
    which held while the app could not answer at all. Now that an approval is a modal,
    the app names the type twice — once as the callback's parameter type, once to check
    what the modal handed back — and neither is the thing worth forbidding. What must
    stay impossible is *construction*: the keystroke-to-decision mapping belongs to
    ``ui.reduce.decision_for``, where it is a table with unit tests, and an app that
    could call ``ApprovalDecision(approved=True)`` could approve an edit nobody saw.
    So the check is now on the syntax tree — every call in the module, by name — which
    is both stricter than the substring (it cannot be satisfied by a rename) and immune
    to a type annotation looking like a violation.
    """
    tree = ast.parse(inspect.getsource(app_module))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "ApprovalDecision"
    ]
    # It builds exactly one, for a modal that vanished without an answer, and refusing is
    # the only safe reading of that. Every construction here must be a refusal.
    assert calls, "expected the dismissal fallback; if it is gone, drop this test with it"
    for call in calls:
        approved = next((kw.value for kw in call.keywords if kw.arg == "approved"), None)
        assert isinstance(approved, ast.Constant) and approved.value is False, (
            "the app built an ApprovalDecision that was not a refusal"
        )

    named = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "Answer" not in named, "nor may it build the policy layer's answer type"


# --------------------------------------------------------------------------- #
# multi_turn_events — the multi-turn driver, offline over a scripted run_turn
# --------------------------------------------------------------------------- #


async def _reply_turn(prompt: str, seen: list[str]) -> AsyncIterator[Event]:
    """A scripted 'turn': record the prompt, emit one text event. No model."""
    seen.append(prompt)
    yield TextDelta(text=f"reply to {prompt}")


async def test_multi_turn_events_runs_the_first_prompt_then_each_queued_follow_up() -> None:
    seen: list[str] = []
    submissions: asyncio.Queue[str | None] = asyncio.Queue()
    submissions.put_nowait("second")
    submissions.put_nowait(None)  # end the stream after the follow-up
    events = [
        e async for e in multi_turn_events("first", submissions, lambda p: _reply_turn(p, seen))
    ]
    assert seen == ["first", "second"], "the argv prompt runs, then the queued follow-up"
    assert [e.text for e in events if isinstance(e, TextDelta)] == [
        "reply to first",
        "reply to second",
    ]


async def test_multi_turn_events_ends_when_none_is_queued() -> None:
    seen: list[str] = []
    submissions: asyncio.Queue[str | None] = asyncio.Queue()
    submissions.put_nowait(None)  # nothing follows the first turn
    events = [
        e async for e in multi_turn_events("only", submissions, lambda p: _reply_turn(p, seen))
    ]
    assert seen == ["only"]
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["reply to only"]


async def test_multi_turn_events_blocks_for_the_next_prompt_rather_than_ending() -> None:
    """Between turns the driver awaits the queue — it does not finish when a turn's events
    run out, which is what makes the session multi-turn rather than one-and-done.

    Driven with a background consumer task (never cancelled) rather than a timeout on
    ``__anext__``: cancelling an in-flight ``__anext__`` finalises the async generator, so
    that technique would prove the opposite of what it claims. Instead we watch the task
    stay *pending* while the queue is empty, and advance only when a prompt arrives.
    """
    seen: list[str] = []
    collected: list[Event] = []
    submissions: asyncio.Queue[str | None] = asyncio.Queue()

    async def consume() -> None:
        async for event in multi_turn_events("first", submissions, lambda p: _reply_turn(p, seen)):
            collected.append(event)

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)  # let the first turn drain
    assert [e.text for e in collected if isinstance(e, TextDelta)] == ["reply to first"]
    assert not task.done(), "the driver ended after one turn instead of awaiting the next"

    submissions.put_nowait("second")
    await asyncio.sleep(0.05)
    assert [e.text for e in collected if isinstance(e, TextDelta)] == [
        "reply to first",
        "reply to second",
    ]
    assert not task.done(), "still multi-turn: awaiting the next prompt, not finished"
    assert seen == ["first", "second"]

    submissions.put_nowait(None)  # end it
    await asyncio.wait_for(task, timeout=1.0)
    assert task.done()
