"""The TUI as the default surface: reachable with no prompt, and slash commands work.

The session audit found the default start (`ronin`, no arguments) landed in the line
REPL, while the richer Textual view needed a prompt on argv — and that the view it could
not reach silently forwarded `/help` to the model as a question. Between them that made
the default experience the weaker one, on every session.

These tests pin the two halves: `multi_turn_events` waits for a first message instead of
running an empty turn, and a submitted slash command is answered locally and shown in its
own region rather than sent to the model.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from ui_harness import stream

from ronin.core.types import Event, TurnEnd, TurnStart, TurnState
from ronin.ui.app import multi_turn_events
from ronin.ui.commands import is_command
from ronin.ui.reduce import NOTICE_HISTORY, ViewState
from ronin.ui.render import PLAIN, render_notices, render_panels

# --------------------------------------------------------------------------- #
# starting with nothing — what a bare `ronin` does
# --------------------------------------------------------------------------- #


async def test_no_first_prompt_waits_instead_of_running_an_empty_turn() -> None:
    queue: asyncio.Queue[str | None] = asyncio.Queue()
    ran: list[str] = []

    async def run_turn(prompt: str) -> AsyncIterator[Event]:
        ran.append(prompt)
        yield TurnStart(turn_index=0)
        yield TurnEnd(turn_index=0, state=TurnState.DONE)

    queue.put_nowait("the first thing I typed")
    queue.put_nowait(None)
    events = [event async for event in multi_turn_events("", queue, run_turn)]

    assert ran == ["the first thing I typed"]  # the empty prompt never became a turn
    assert any(isinstance(event, TurnStart) for event in events)


async def test_none_is_treated_the_same_as_an_empty_first_prompt() -> None:
    queue: asyncio.Queue[str | None] = asyncio.Queue()
    ran: list[str] = []

    async def run_turn(prompt: str) -> AsyncIterator[Event]:
        ran.append(prompt)
        yield TurnEnd(turn_index=0, state=TurnState.DONE)

    queue.put_nowait("typed later")
    queue.put_nowait(None)
    [event async for event in multi_turn_events(None, queue, run_turn)]
    assert ran == ["typed later"]


async def test_an_argv_prompt_still_runs_first_exactly_as_before() -> None:
    queue: asyncio.Queue[str | None] = asyncio.Queue()
    ran: list[str] = []

    async def run_turn(prompt: str) -> AsyncIterator[Event]:
        ran.append(prompt)
        yield TurnEnd(turn_index=0, state=TurnState.DONE)

    queue.put_nowait("and then this")
    queue.put_nowait(None)
    [event async for event in multi_turn_events("from argv", queue, run_turn)]
    assert ran == ["from argv", "and then this"]  # unchanged for the old entry point


# --------------------------------------------------------------------------- #
# notices — where a local answer goes
# --------------------------------------------------------------------------- #


def test_a_notice_renders_in_its_own_region_not_the_transcript() -> None:
    state = ViewState(committed_text="the model said this").with_notice("/cost → $0.0456")
    panels = render_panels(state, styles=PLAIN)
    assert "/cost → $0.0456" in panels.notices
    # The transcript stays what the *model* said — two voices, kept apart.
    assert "/cost" not in panels.transcript
    assert "the model said this" in panels.transcript


def test_notices_are_bounded_so_a_session_cannot_accumulate_them() -> None:
    state = ViewState()
    for index in range(NOTICE_HISTORY + 10):
        state = state.with_notice(f"answer {index}")
    assert len(state.notices) == NOTICE_HISTORY
    assert state.notices[-1] == f"answer {NOTICE_HISTORY + 9}"  # newest kept


def test_an_empty_notice_is_not_recorded() -> None:
    # A command that printed nothing should not leave a blank row behind.
    assert ViewState().with_notice("").notices == ()


def test_notices_clear_when_a_new_turn_starts() -> None:
    state = ViewState().with_notice("/diff → 3 files changed")
    assert state.cleared_notices().notices == ()
    assert ViewState().cleared_notices().notices == ()  # already empty: a no-op


def test_a_settled_screen_with_no_notices_renders_nothing() -> None:
    assert render_notices(ViewState()) == ""


def test_multi_line_command_output_survives_intact() -> None:
    table = "command  what it does\n/help    this table\n/cost    session spend"
    assert render_notices(ViewState().with_notice(table)).count("\n") == 2


# --------------------------------------------------------------------------- #
# routing — what counts as a command
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("line", ["/help", "/cost", "/model sonnet", "  /diff"])
def test_slash_lines_are_recognised_as_commands(line: str) -> None:
    assert is_command(line)


@pytest.mark.parametrize("line", ["fix the bug", "what does / mean?", "a/b testing", ""])
def test_ordinary_prose_is_not_a_command(line: str) -> None:
    assert not is_command(line)


# --------------------------------------------------------------------------- #
# the real app: a slash command is answered locally, never sent to the model
# --------------------------------------------------------------------------- #

pytest.importorskip("textual", reason="the interactive TUI needs the 'tui' extra")


async def test_a_slash_command_in_the_tui_runs_instead_of_reaching_the_model() -> None:
    from textual.widgets import Static, TextArea

    from ronin.ui.app import NOTICES_ID, Session, _build_app

    submitted: list[str] = []
    commanded: list[str] = []

    async def on_command(line: str) -> str:
        commanded.append(line)
        return "command  what it does\n/help    this table"

    app = _build_app(
        Session(
            events=stream(()),
            on_submit=submitted.append,
            on_command=on_command,
        )
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        prompt = app.query_one("#prompt-input", TextArea)
        prompt.focus()
        await pilot.pause()
        prompt.text = "/help"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        assert commanded == ["/help"]
        assert submitted == []  # the model was never asked about /help
        assert "this table" in str(app.query_one(f"#{NOTICES_ID}", Static).visual)


async def test_ordinary_prose_still_goes_to_the_model() -> None:
    from textual.widgets import TextArea

    from ronin.ui.app import Session, _build_app

    submitted: list[str] = []
    commanded: list[str] = []

    async def on_command(line: str) -> str:  # pragma: no cover - must not be reached
        commanded.append(line)
        return ""

    app = _build_app(Session(events=stream(()), on_submit=submitted.append, on_command=on_command))
    async with app.run_test() as pilot:
        await pilot.pause()
        prompt = app.query_one("#prompt-input", TextArea)
        prompt.focus()
        await pilot.pause()
        prompt.text = "fix the failing test"
        await pilot.press("enter")
        await pilot.pause()
        assert submitted == ["fix the failing test"]
        assert commanded == []


async def test_without_a_command_handler_a_slash_line_is_just_text() -> None:
    """The demo and replay paths set no handler; nothing may silently swallow input."""
    from textual.widgets import TextArea

    from ronin.ui.app import Session, _build_app

    submitted: list[str] = []
    app = _build_app(Session(events=stream(()), on_submit=submitted.append))
    async with app.run_test() as pilot:
        await pilot.pause()
        line = app.query_one("#prompt-input", TextArea)
        line.focus()
        await pilot.pause()
        line.text = "/help"
        await pilot.press("enter")
        await pilot.pause()
        assert submitted == ["/help"]
