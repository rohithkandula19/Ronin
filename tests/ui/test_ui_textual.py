"""The Textual app, driven for real through Textual's headless pilot.

Skipped when the ``tui`` extra is absent — every other module in ``ronin.ui`` is
tested without it, and this file is the only one that needs it. No terminal is
required: ``App.run_test()`` runs the app headless and ``Pilot`` presses keys, so
this is still an offline, no-network test.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from ui_harness import APPROVAL, approval_turn, happy_turn, stream

from ronin.core.types import (
    ApprovalDecision,
    Event,
    Mode,
    TextDelta,
    TurnEnd,
    TurnStart,
    TurnState,
)
from ronin.ui.app import (
    APPROVAL_ID,
    MODAL_ID,
    STATUS_ID,
    TODOS_ID,
    TOOLS_ID,
    TRANSCRIPT_ID,
    Asking,
    Session,
    _build_app,
)

pytest.importorskip("textual", reason="the interactive TUI needs the 'tui' extra")


def _text(app: object, region: str) -> str:
    """What a region currently shows, as plain text."""
    from textual.widgets import Static

    widget = app.query_one(f"#{region}", Static)  # type: ignore[attr-defined]
    # `visual` is the parsed content: markup resolved, so this is what a human sees.
    return str(widget.visual)


def _modal_text(app: object) -> str:
    """What the approval modal shows. Queried off the *active* screen, not the app: a
    modal is a screen pushed on top, so `app.query_one` would search past it."""
    from textual.widgets import Static

    screen = app.screen  # type: ignore[attr-defined]
    return str(screen.query_one(f"#{MODAL_ID}", Static).visual)


async def test_the_app_paints_every_region_from_the_scripted_stream() -> None:
    app = _build_app(Session(events=stream(happy_turn()), model="claude-sonnet", branch="main"))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "Reading src/main.py now." in _text(app, TRANSCRIPT_ID)
        assert "Read(src/main.py)" in _text(app, TOOLS_ID)
        assert "240 lines" in _text(app, TOOLS_ID)
        assert "read the test" in _text(app, TODOS_ID)
        assert "claude-sonnet" in _text(app, STATUS_ID)
        assert "main" in _text(app, STATUS_ID)
        assert _text(app, APPROVAL_ID) == ""


async def test_a_dropped_stream_is_not_rendered_twice_in_the_widget() -> None:
    app = _build_app(Session(events=stream(happy_turn())))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "DUPLICATE" not in _text(app, TRANSCRIPT_ID)


async def test_the_diff_reaches_the_approval_region_before_any_decision() -> None:
    # Cut the script at the ApprovalRequest: the region must show the command while
    # the decision is still outstanding, which is the whole point of the gate.
    pending = approval_turn()[:4]
    app = _build_app(Session(events=stream(pending)))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert APPROVAL.rendered in _text(app, APPROVAL_ID)


async def _answer_with(key: str) -> list[ApprovalDecision]:
    """Run the app, ask through the attached coroutine, press ``key``, return the answer."""
    asked: list[Asking] = []
    app = _build_app(Session(events=stream(approval_turn()[:4]), on_attach=asked.append))
    answers: list[ApprovalDecision] = []
    async with app.run_test() as pilot:
        await pilot.pause()
        assert asked, "the app never offered a way to ask"

        async def ask() -> None:
            answers.append(await asked[0](APPROVAL))

        # A Textual *worker*, not a bare task: `push_screen_wait` refuses to be awaited
        # from the message pump, and in production its only caller is `_consume`, which is
        # a worker for the same reason. Driving it any other way here would be testing an
        # arrangement that cannot occur.
        worker = app.run_worker(ask(), exclusive=False)
        await pilot.pause()
        assert APPROVAL.rendered in _modal_text(app), "the modal shows what is decided"
        await pilot.press(key)
        await pilot.pause()
        await worker.wait()
    return answers


@pytest.mark.parametrize(
    ("key", "approved", "remember"),
    [("y", True, False), ("a", True, True), ("n", False, False), ("escape", False, False)],
)
async def test_a_keypress_on_the_modal_answers_the_policy_s_question(
    key: str, approved: bool, remember: bool
) -> None:
    """The whole point of this phase: before it, no key in the real app could approve
    anything — ``cli`` never attached an answer path and there was no modal to press a key
    on. Driven through the actual widget, so it covers what a unit test cannot: that the
    modal takes focus and receives the key."""
    answers = await _answer_with(key)
    assert [(a.approved, a.remember) for a in answers] == [(approved, remember)]


async def test_a_key_that_answers_nothing_leaves_the_question_open() -> None:
    """A stray keystroke must not resolve an approval in either direction. Denying on every
    unrecognised key would turn a typo into a refused edit; approving would be
    indefensible. So the modal stays up and the human still has to answer."""
    asked: list[Asking] = []
    app = _build_app(Session(events=stream(approval_turn()[:4]), on_attach=asked.append))
    answers: list[ApprovalDecision] = []
    async with app.run_test() as pilot:
        await pilot.pause()

        async def ask() -> None:
            answers.append(await asked[0](APPROVAL))

        worker = app.run_worker(ask(), exclusive=False)
        await pilot.pause()
        await pilot.press("j")
        await pilot.pause()
        assert answers == [], "an unmapped key answered the question"
        assert APPROVAL.rendered in _modal_text(app), "still asking"
        await pilot.press("y")
        await pilot.pause()
        await worker.wait()
    assert [a.approved for a in answers] == [True]


async def test_escape_on_the_modal_denies_without_also_interrupting_the_turn() -> None:
    """``escape`` means two things in this app — deny here, interrupt everywhere else — and
    one keypress must not do both. A denial that also cancelled the turn would make "no,
    not that one" indistinguishable from "stop working"."""
    interrupts: list[str] = []
    asked: list[Asking] = []
    session = Session(
        events=stream(approval_turn()[:4]),
        on_attach=asked.append,
        on_interrupt=lambda: interrupts.append("interrupt"),
    )
    app = _build_app(session)
    answers: list[ApprovalDecision] = []
    async with app.run_test() as pilot:
        await pilot.pause()

        async def ask() -> None:
            answers.append(await asked[0](APPROVAL))

        worker = app.run_worker(ask(), exclusive=False)
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        await worker.wait()
    assert [a.approved for a in answers] == [False]
    assert interrupts == [], "escape denied the approval and also cancelled the turn"


async def test_no_modal_appears_when_nothing_attached_an_answer_path() -> None:
    """With ``on_attach`` unset nobody can ask, so no modal is ever raised and the policy
    engine refuses on its own. The request still renders in its region — the demo and a
    replayed recording rely on that."""
    app = _build_app(Session(events=stream(approval_turn()[:4])))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert APPROVAL.rendered in _text(app, APPROVAL_ID)
        from textual.css.query import NoMatches

        # The real exception type, not a message pattern: "there is no modal" is the
        # claim, and a substring of Textual's wording could start matching something else.
        with pytest.raises(NoMatches):
            _modal_text(app)


async def test_the_status_line_asks_the_orchestrator_for_context_occupancy() -> None:
    """The reducer cannot compute this: the loop reports cumulative spend, which is not how
    full the window is. So the orchestrator is asked, once per ``TurnEnd``."""
    app = _build_app(Session(events=stream(happy_turn()), on_status=lambda: 0.42))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "42% ctx" in _text(app, STATUS_ID)


async def test_the_approval_region_clears_once_the_tool_reports_back() -> None:
    app = _build_app(Session(events=stream(approval_turn())))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert _text(app, APPROVAL_ID) == ""


async def test_text_appears_before_the_stream_ends() -> None:
    # A producer that blocks after the first delta: the assertion is that the
    # widget already shows it while the stream is still open.
    gate = asyncio.Event()

    async def paused() -> AsyncIterator[Event]:
        yield TurnStart(turn_index=0)
        yield TextDelta(text="first chunk")
        await gate.wait()
        yield TextDelta(text=" second chunk")
        yield TurnEnd(turn_index=0, state=TurnState.DONE, stop_reason="no_tool_calls")

    app = _build_app(Session(events=paused()))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert _text(app, TRANSCRIPT_ID) == "first chunk"
        gate.set()
        await pilot.pause()
        assert _text(app, TRANSCRIPT_ID) == "first chunk second chunk"


async def test_model_text_that_looks_like_markup_is_shown_literally() -> None:
    # Without escaping, Textual's Static would parse `[red]` as a tag and the words
    # would vanish from the transcript.
    async def tricky() -> AsyncIterator[Event]:
        yield TurnStart(turn_index=0)
        yield TextDelta(text="use items[0] and [red]this literal tag[/red]")
        yield TurnEnd(turn_index=0, state=TurnState.DONE, stop_reason="no_tool_calls")

    app = _build_app(Session(events=tricky()))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert _text(app, TRANSCRIPT_ID) == "use items[0] and [red]this literal tag[/red]"


async def test_shift_tab_cycles_the_mode_and_repaints_the_status_line() -> None:
    seen: list[Mode] = []
    app = _build_app(Session(events=stream(()), on_mode_change=seen.append))
    async with app.run_test() as pilot:
        await pilot.press("shift+tab")
        assert "auto-accept" in _text(app, STATUS_ID)
        await pilot.press("shift+tab")
        assert "plan" in _text(app, STATUS_ID)
        await pilot.press("shift+tab")
        assert "normal" in _text(app, STATUS_ID)
    assert seen == [Mode.AUTO_EDIT, Mode.PLAN, Mode.ASK]


async def test_escape_interrupts_and_a_second_escape_rewinds() -> None:
    interrupts: list[int] = []
    rewinds: list[int] = []
    app = _build_app(
        Session(
            events=stream(()),
            on_interrupt=lambda: interrupts.append(1),
            on_rewind=rewinds.append,
        )
    )
    async with app.run_test() as pilot:
        await pilot.press("escape")
        assert interrupts == [1]
        assert rewinds == []
        # inside the double-press window, so this escalates rather than interrupting
        await pilot.press("escape")
        assert interrupts == [1]
        assert rewinds == [0]


async def test_a_slow_double_press_interrupts_twice() -> None:
    interrupts: list[int] = []
    rewinds: list[int] = []
    ticks = iter([0.0, 100.0])
    app = _build_app(
        Session(
            events=stream(()),
            on_interrupt=lambda: interrupts.append(1),
            on_rewind=rewinds.append,
        )
    )
    async with app.run_test() as pilot:
        app.keys.clock = lambda: next(ticks)
        await pilot.press("escape")
        await pilot.press("escape")
        assert interrupts == [1, 1]
        assert rewinds == []


async def test_the_app_never_constructs_an_approval_decision() -> None:
    requests: list[str] = []
    app = _build_app(
        Session(events=stream(approval_turn()), on_approval=lambda r: requests.append(r.name))
    )
    async with app.run_test() as pilot:
        await pilot.pause()
    # The request is handed out; the answer is somebody else's job.
    assert requests == ["Bash"]
