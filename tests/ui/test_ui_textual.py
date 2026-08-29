"""The Textual app, driven for real through Textual's headless pilot.

Skipped when the ``tui`` extra is absent — every other module in ``ronin.ui`` is
tested without it, and this file is the only one that needs it. No terminal is
required: ``App.run_test()`` runs the app headless and ``Pilot`` presses keys, so
this is still an offline, no-network test.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
from ui_harness import APPROVAL, approval_turn, happy_turn, stream

from ronin.core.steering import Steering
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
    INPUT_ID,
    MENTIONS_ID,
    MODAL_ID,
    QUEUED_ID,
    REASON_ID,
    STATUS_ID,
    TODOS_ID,
    TOOLS_ID,
    TRANSCRIPT_ID,
    Asking,
    Session,
    _build_app,
)
from ronin.ui.reduce import REASON_KEY
from ronin.ui.render import APPROVAL_PROMPT, REASON_PROMPT

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


async def _deny_with_reason(*keys: str) -> tuple[list[ApprovalDecision], list[str]]:
    """Press ``keys`` on the modal; return the answers and the prompt text at the end.

    Returns the prompt too, because half of what this feature has to get right is what
    the screen says while it waits — a prompt with no stated way out is how someone
    force-quits instead of backing out of one keystroke.
    """
    asked: list[Asking] = []
    app = _build_app(Session(events=stream(approval_turn()[:4]), on_attach=asked.append))
    answers: list[ApprovalDecision] = []
    seen: list[str] = []
    async with app.run_test() as pilot:
        await pilot.pause()

        async def ask() -> None:
            answers.append(await asked[0](APPROVAL))

        worker = app.run_worker(ask(), exclusive=False)
        await pilot.pause()
        for key in keys:
            await pilot.press(key)
            await pilot.pause()
        seen.append(_modal_text(app) if not answers else "")
        if answers:
            await worker.wait()
        else:
            worker.cancel()
    return answers, seen


async def test_the_reason_line_is_out_of_the_focus_order_until_it_is_needed() -> None:
    """Phase one must behave exactly as it did before the line existed.

    Textual focuses the first focusable widget when a screen mounts, so an enabled
    ``Input`` on this modal captures the very keystroke that approves — the request then
    never resolves and the turn waits forever. That regression shows up as a *hung*
    suite rather than a failing one, which is a bad signal to leave for CI, so this
    asserts the disabled state directly and fails in milliseconds instead.
    """
    from textual.widgets import Input

    asked: list[Asking] = []
    app = _build_app(Session(events=stream(approval_turn()[:4]), on_attach=asked.append))
    async with app.run_test() as pilot:
        await pilot.pause()

        async def ask() -> None:
            await asked[0](APPROVAL)

        worker = app.run_worker(ask(), exclusive=False)
        await pilot.pause()
        screen = app.screen
        line = screen.query_one(f"#{REASON_ID}", Input)

        assert line.disabled is True, "an enabled reason line swallows the approving key"
        assert line.display is False
        assert screen.focused is not line
        worker.cancel()


async def test_the_reason_key_denies_with_the_words_the_human_typed() -> None:
    """The outcome ``policy.py`` calls the one that makes the gate usable.

    Its transport was already complete — ``ApprovalDecision.reason`` becomes
    ``Answer.feedback`` in ``cli.approve.answer_for`` and the engine reproduces it
    verbatim. The only missing piece was a way for a person to type the sentence, and
    one keystroke cannot carry a sentence, which is why the modal grew a second phase.
    """
    answers, _ = await _deny_with_reason(REASON_KEY, *"use staging", "enter")

    assert len(answers) == 1
    assert answers[0].approved is False
    assert answers[0].reason == "use staging"
    assert answers[0].remember is False, "a denial has nothing to remember"


async def test_escape_while_typing_a_reason_keeps_the_request_open() -> None:
    """The requirement that makes the second phase safe rather than a trap.

    ``escape`` denies in phase one. In phase two it has to mean "I take back the
    keystroke", not "deny with whatever I have typed" and not "deny with nothing" —
    someone who opened the reason line and thought better of it has decided nothing.
    So the request must still be standing afterwards, answerable either way.
    """
    answers, seen = await _deny_with_reason(REASON_KEY, *"never", "escape")

    assert answers == [], "escape during collection must not resolve the approval"
    assert APPROVAL.rendered in seen[0], "the command is still on screen to decide on"
    assert APPROVAL_PROMPT in seen[0], "and the ordinary key list is back"


async def test_the_request_stays_answerable_after_backing_out_of_a_reason() -> None:
    """Not merely unresolved — still *answerable*. An approval that survives the cancel
    but no longer takes a keypress is a hung turn, which is worse than either answer."""
    answers, _ = await _deny_with_reason(REASON_KEY, "escape", "y")

    assert [(a.approved, a.remember) for a in answers] == [(True, False)]


async def test_the_reason_line_names_the_way_out_while_it_waits() -> None:
    """Phase two swaps the key list for a prompt that says what enter and esc do."""
    _answers, seen = await _deny_with_reason(REASON_KEY)

    assert REASON_PROMPT in seen[0]
    assert APPROVAL.rendered in seen[0], "the command stays visible while you explain"


async def test_an_empty_reason_is_the_ordinary_denial_not_an_empty_correction() -> None:
    """Pressing the reason key then enter must never be worse than pressing ``n``.

    The reason is left *blank* rather than filled with a stand-in: the engine already
    branches on empty feedback and says something better than this layer could, while a
    placeholder would render as "the user declined and said: the user declined this
    action". ``tests/cli/test_ronin_cli_approve.py`` pins the text the model then sees.
    """
    answers, _ = await _deny_with_reason(REASON_KEY, "enter")

    assert len(answers) == 1
    assert answers[0].approved is False
    assert answers[0].reason == ""


async def test_the_reason_key_alone_does_not_answer_the_question() -> None:
    """It opens a line; it decides nothing. If it resolved on its own it would be a
    second denial key with a confusing name."""
    answers, _ = await _deny_with_reason(REASON_KEY)

    assert answers == []


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

    async def on_rewind(index: int) -> str:
        rewinds.append(index)
        return f"rewound to turn {index}"

    app = _build_app(
        Session(
            events=stream(()),
            on_interrupt=lambda: interrupts.append(1),
            on_rewind=on_rewind,
        )
    )
    async with app.run_test() as pilot:
        await pilot.press("escape")
        await pilot.pause()
        assert interrupts == [1]
        assert rewinds == []
        # inside the double-press window, so this escalates rather than interrupting
        await pilot.press("escape")
        # the rewind runs on a worker (it restores files and truncates the transcript),
        # so wait for it rather than reading the list on the next line
        await app.workers.wait_for_complete()
        assert interrupts == [1]
        assert rewinds == [0]


async def test_a_slow_double_press_interrupts_twice() -> None:
    interrupts: list[int] = []
    rewinds: list[int] = []
    ticks = iter([0.0, 100.0])

    async def on_rewind(index: int) -> str:
        rewinds.append(index)
        return "rewound"

    app = _build_app(
        Session(
            events=stream(()),
            on_interrupt=lambda: interrupts.append(1),
            on_rewind=on_rewind,
        )
    )
    async with app.run_test() as pilot:
        app.keys.clock = lambda: next(ticks)
        await pilot.press("escape")
        await pilot.press("escape")
        await app.workers.wait_for_complete()
        assert interrupts == [1, 1]
        assert rewinds == [], "outside the window, both presses interrupt and neither rewinds"


async def test_a_rewind_notice_is_shown_to_the_user() -> None:
    # The user-visible half: what `on_rewind` returns is surfaced as a notification, so
    # the user learns their files and transcript moved back rather than guessing.
    async def on_rewind(index: int) -> str:
        return "rewound one turn and restored the work tree"

    app = _build_app(Session(events=stream(()), on_rewind=on_rewind))
    async with app.run_test() as pilot:
        await pilot.press("escape")
        await pilot.press("escape")
        await app.workers.wait_for_complete()
        await pilot.pause()
        messages = [note.message for note in app._notifications]
    assert "rewound one turn and restored the work tree" in messages


async def test_the_app_never_constructs_an_approval_decision() -> None:
    requests: list[str] = []
    app = _build_app(
        Session(events=stream(approval_turn()), on_approval=lambda r: requests.append(r.name))
    )
    async with app.run_test() as pilot:
        await pilot.pause()
    # The request is handed out; the answer is somebody else's job.
    assert requests == ["Bash"]


# --------------------------------------------------------------------------- #
# the multi-turn input line, driven for real through the pilot
# --------------------------------------------------------------------------- #


def _input(app: object) -> Any:
    from textual.widgets import Input

    return app.query_one(f"#{INPUT_ID}", Input)  # type: ignore[attr-defined]


async def test_a_submitted_message_reaches_on_submit_and_clears_the_line() -> None:
    submitted: list[str] = []
    app = _build_app(Session(events=stream(happy_turn()), on_submit=submitted.append))
    async with app.run_test() as pilot:
        await pilot.pause()
        line = _input(app)
        line.focus()
        await pilot.pause()
        line.value = "delete the temp files"
        await pilot.press("enter")
        await pilot.pause()
    assert submitted == ["delete the temp files"], "Enter hands the message to on_submit"
    assert line.value == "", "the line clears after a submit"


async def _typed(app: object, pilot: Any, text: str) -> None:
    """Put ``text`` on the focused prompt line and send it."""
    line = _input(app)
    line.value = text
    await pilot.press("enter")
    await pilot.pause()


async def test_up_and_down_walk_the_prompts_that_were_sent() -> None:
    """The pure walk is covered in ``test_ui_history.py``; this is the wiring.

    Driven through the real widget because that is the half a unit test cannot reach:
    that the arrows are not swallowed by Textual's ``Input``, that the app sees them,
    and that the recalled text lands in the box.
    """
    submitted: list[str] = []
    app = _build_app(Session(events=stream(happy_turn()), on_submit=submitted.append))
    async with app.run_test() as pilot:
        await pilot.pause()
        line = _input(app)
        line.focus()
        await pilot.pause()
        await _typed(app, pilot, "first prompt")
        await _typed(app, pilot, "second prompt")

        await pilot.press("up")
        await pilot.pause()
        assert line.value == "second prompt"
        await pilot.press("up")
        await pilot.pause()
        assert line.value == "first prompt"
        await pilot.press("down")
        await pilot.pause()
        assert line.value == "second prompt"


async def test_a_half_written_prompt_survives_a_trip_through_history() -> None:
    """The behaviour that makes the keystroke safe to press at all.

    Someone mid-sentence who checks what they asked earlier must get their own words
    back. Losing them is how a history key becomes something users learn to avoid.
    """
    app = _build_app(Session(events=stream(happy_turn()), on_submit=lambda _text: None))
    async with app.run_test() as pilot:
        await pilot.pause()
        line = _input(app)
        line.focus()
        await pilot.pause()
        await _typed(app, pilot, "an earlier prompt")

        line.value = "half written"
        await pilot.press("up")
        await pilot.pause()
        assert line.value == "an earlier prompt"
        await pilot.press("down")
        await pilot.pause()

        assert line.value == "half written", "the draft did not come back"


async def test_up_with_nothing_in_history_leaves_the_line_alone() -> None:
    """A history key that clears the box on an empty history destroys work for no gain."""
    app = _build_app(Session(events=stream(happy_turn()), on_submit=lambda _text: None))
    async with app.run_test() as pilot:
        await pilot.pause()
        line = _input(app)
        line.focus()
        await pilot.pause()
        line.value = "nothing sent yet"

        await pilot.press("up")
        await pilot.pause()

        assert line.value == "nothing sent yet"


async def test_the_cursor_lands_at_the_end_of_a_recalled_prompt() -> None:
    """You recall a prompt to edit it, and editing starts where you would type."""
    app = _build_app(Session(events=stream(happy_turn()), on_submit=lambda _text: None))
    async with app.run_test() as pilot:
        await pilot.pause()
        line = _input(app)
        line.focus()
        await pilot.pause()
        await _typed(app, pilot, "a long-ish earlier prompt")

        await pilot.press("up")
        await pilot.pause()

        assert line.cursor_position == len("a long-ish earlier prompt")


async def test_a_slash_command_is_recalled_too() -> None:
    """`/model sonnet` is exactly the sort of line someone retypes, so it is recorded
    before the command/prompt fork rather than after it."""
    ran: list[str] = []

    async def on_command(line: str) -> str:
        ran.append(line)
        return "ok"

    app = _build_app(
        Session(
            events=stream(happy_turn()),
            on_submit=lambda _text: None,
            on_command=on_command,
        )
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        line = _input(app)
        line.focus()
        await pilot.pause()
        await _typed(app, pilot, "/cost")
        await pilot.pause()

        await pilot.press("up")
        await pilot.pause()

        assert ran == ["/cost"], "the command still ran"
        assert line.value == "/cost", "and it is recallable"


async def test_the_arrows_are_left_alone_when_the_prompt_line_is_not_focused() -> None:
    """Not a global hijack. With focus elsewhere the arrows keep whatever meaning they
    have there, which is what leaves room for a scrollable transcript or a future
    multi-line editor."""
    app = _build_app(Session(events=stream(happy_turn()), on_submit=lambda _text: None))
    async with app.run_test() as pilot:
        await pilot.pause()
        line = _input(app)
        line.focus()
        await pilot.pause()
        await _typed(app, pilot, "recorded")
        line.value = "still here"
        app.set_focus(None)
        await pilot.pause()

        await pilot.press("up")
        await pilot.pause()

        assert line.value == "still here", "history moved with the line unfocused"


async def test_a_blank_submit_is_not_a_turn() -> None:
    submitted: list[str] = []
    app = _build_app(Session(events=stream(happy_turn()), on_submit=submitted.append))
    async with app.run_test() as pilot:
        await pilot.pause()
        line = _input(app)
        line.focus()
        await pilot.pause()
        line.value = "   "  # whitespace only
        await pilot.press("enter")
        await pilot.pause()
    assert submitted == [], "a blank/whitespace submit is dropped, not run"


async def test_the_input_line_is_present_even_with_no_on_submit() -> None:
    # Demo / replay: the line renders (so the layout is stable) but a submit has nowhere
    # to go, and the handler simply does nothing rather than erroring.
    app = _build_app(Session(events=stream(happy_turn())))
    async with app.run_test() as pilot:
        await pilot.pause()
        line = _input(app)
        line.focus()
        await pilot.pause()
        line.value = "ignored"
        await pilot.press("enter")
        await pilot.pause()
        assert line.value == "", "still cleared, even with nothing consuming it"


# --------------------------------------------------------------------------- #
# Steering: a message typed mid-turn joins the running conversation
# --------------------------------------------------------------------------- #


def _running_turn() -> tuple[Event, ...]:
    """A turn that has started and not ended, so the app is genuinely ``busy``."""
    return happy_turn()[:8]


def _turn_in_flight() -> tuple[AsyncIterator[Event], asyncio.Event]:
    """A turn that stays in flight until released, then ends. Release before teardown.

    Truncating the stream to leave the app ``busy`` is not enough on its own, and the
    difference is a race a slow runner loses: ``_tick`` repaints ten times a second
    while ``state.busy``, so a timer callback can land after ``run_test()`` has taken
    the widget tree apart, and ``_paint`` then raises ``NoMatches``. Ending the turn
    before leaving the block closes that window — ``_tick`` returns early once the
    state is finished.

    Reproduced by shrinking ``SPINNER_INTERVAL_SECONDS`` to 0.5ms, which turns the
    race into a certainty; with the release in place it passes at that interval too.
    """
    release = asyncio.Event()

    async def events() -> AsyncIterator[Event]:
        for event in _running_turn():
            yield event
        await release.wait()
        yield TurnEnd(
            turn_index=0,
            state=TurnState.DONE,
            stop_reason="no_tool_calls",
            agent_state=None,
        )

    return events(), release


async def test_a_message_typed_mid_turn_steers_instead_of_queueing_a_new_turn() -> None:
    """The whole feature, through the real widget: while the agent is working, Enter must
    reach the steering seam and not the next-turn queue. Driven here rather than only in
    the pure tests because the branch is in the submit handler, and the thing that decides
    it — ``state.busy`` — is only true once real events have been folded in."""
    steering = Steering()
    submitted: list[str] = []
    events, release = _turn_in_flight()
    app = _build_app(
        Session(
            events=events,
            on_submit=submitted.append,
            on_steer=steering.push,
            on_steering=steering.pending,
        )
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.state.busy, "the fixture must leave a turn in flight for this to mean anything"
        line = _input(app)
        line.focus()
        await pilot.pause()
        line.value = "actually use pathlib"
        await pilot.press("enter")
        await pilot.pause()

        assert steering.pending() == ("actually use pathlib",)
        assert submitted == [], "a mid-turn message must not also start a new turn"
        assert line.value == ""
        shown = _text(app, QUEUED_ID)
        assert "actually use pathlib" in shown
        assert "next step" in shown, "the line has to say when it lands, or esc is a guess"

        release.set()
        await pilot.pause()


async def test_a_message_typed_between_turns_starts_a_turn_rather_than_steering() -> None:
    steering = Steering()
    submitted: list[str] = []
    app = _build_app(
        Session(
            events=stream(happy_turn()),  # ends with TurnEnd, so the app is idle
            on_submit=submitted.append,
            on_steer=steering.push,
            on_steering=steering.pending,
        )
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        assert not app.state.busy
        line = _input(app)
        line.focus()
        await pilot.pause()
        line.value = "now do the other thing"
        await pilot.press("enter")
        await pilot.pause()

        assert submitted == ["now do the other thing"]
        assert steering.pending() == (), "there is no turn to steer between turns"


async def test_with_no_steering_seam_a_mid_turn_message_queues_exactly_as_before() -> None:
    # The demo and a replayed recording have no live loop. Nothing may crash, and the
    # keystroke must still be acknowledged on screen.
    submitted: list[str] = []
    events, release = _turn_in_flight()
    app = _build_app(Session(events=events, on_submit=submitted.append))
    async with app.run_test() as pilot:
        await pilot.pause()
        line = _input(app)
        line.focus()
        await pilot.pause()
        line.value = "held for later"
        await pilot.press("enter")
        await pilot.pause()

        assert submitted == ["held for later"]
        assert "held for later" in _text(app, QUEUED_ID)

        release.set()
        await pilot.pause()


async def test_the_steering_line_clears_when_the_loop_takes_the_message() -> None:
    """The reason the pending list is *pulled*: the loop takes a correction at a moment
    the app cannot see, and a line that only cleared at ``TurnEnd`` would show a message
    delivered two minutes ago as though it were still waiting."""
    steering = Steering()
    gate: asyncio.Event = asyncio.Event()

    async def events() -> AsyncIterator[Event]:
        for event in _running_turn():
            yield event
        await gate.wait()
        # Whatever the loop did with the queue happened before this event arrived.
        yield TextDelta(text=" and now continuing")
        # Ends the turn so the app is not busy at teardown — see `_turn_in_flight`.
        yield TurnEnd(
            turn_index=0,
            state=TurnState.DONE,
            stop_reason="no_tool_calls",
            agent_state=None,
        )

    app = _build_app(Session(events=events(), on_steer=steering.push, on_steering=steering.pending))
    async with app.run_test() as pilot:
        await pilot.pause()
        line = _input(app)
        line.focus()
        await pilot.pause()
        line.value = "use pathlib"
        await pilot.press("enter")
        await pilot.pause()
        assert "use pathlib" in _text(app, QUEUED_ID)

        steering.drain()  # the loop injected it
        gate.set()
        await pilot.pause()

        assert _text(app, QUEUED_ID) == "", "the line must follow the queue down, not wait"
        assert "and now continuing" in _text(app, TRANSCRIPT_ID)


# --------------------------------------------------------------------------- #
# @file mentions: the picker, through the real input widget
# --------------------------------------------------------------------------- #

#: A corpus with two files sharing a basename, so selection actually has to be moved.
MENTION_PATHS = (
    "README.md",
    "apps/api/main.py",
    "src/ronin/cli/main.py",
    "src/ronin/ui/app.py",
)


async def _typed_into(app: object, pilot: Any, text: str) -> Any:
    """Put ``text`` on the focused prompt line one key at a time, as a human would.

    Character by character rather than by assigning ``value``, because the picker is
    driven by ``Input.Changed`` and the cursor position — and a test that sets the value
    wholesale proves nothing about either.
    """
    line = _input(app)
    line.focus()
    await pilot.pause()
    for char in text:
        await pilot.press("at" if char == "@" else char)
    await pilot.pause()
    return line


async def test_typing_a_mention_offers_matching_paths() -> None:
    app = _build_app(Session(events=stream(happy_turn()), on_files=lambda: MENTION_PATHS))
    async with app.run_test() as pilot:
        await pilot.pause()
        await _typed_into(app, pilot, "look at @app")

        shown = _text(app, MENTIONS_ID)
        assert "src/ronin/ui/app.py" in shown
        assert "tab inserts" in shown, "a list with no stated way to take one gets retyped"


async def test_tab_inserts_the_selected_path_and_closes_the_picker() -> None:
    app = _build_app(Session(events=stream(happy_turn()), on_files=lambda: MENTION_PATHS))
    async with app.run_test() as pilot:
        await pilot.pause()
        line = await _typed_into(app, pilot, "look at @app")
        await pilot.press("tab")
        await pilot.pause()

        assert line.value == "look at src/ronin/ui/app.py "
        assert line.cursor_position == len(line.value)
        assert _text(app, MENTIONS_ID) == "", "the picker closes once it has been used"


async def test_the_arrows_choose_a_path_while_the_picker_is_open() -> None:
    app = _build_app(Session(events=stream(happy_turn()), on_files=lambda: MENTION_PATHS))
    async with app.run_test() as pilot:
        await pilot.pause()
        line = await _typed_into(app, pilot, "@main.py")
        assert "apps/api/main.py" in _text(app, MENTIONS_ID)

        await pilot.press("down")
        await pilot.press("tab")
        await pilot.pause()

        assert line.value == "src/ronin/cli/main.py "


async def test_the_arrows_go_back_to_the_history_once_the_picker_is_closed() -> None:
    """The one real key conflict in the feature. The picker wins while it is open; the
    history has to be intact the moment it is not, or ``@`` would cost you the arrows
    for the rest of the session."""
    submitted: list[str] = []
    app = _build_app(
        Session(
            events=stream(happy_turn()),
            on_submit=submitted.append,
            on_files=lambda: MENTION_PATHS,
        )
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        line = await _typed_into(app, pilot, "first message")
        await pilot.press("enter")
        await pilot.pause()

        # A mention, taken — which closes the picker.
        await _typed_into(app, pilot, "@app")
        await pilot.press("tab")
        await pilot.pause()
        assert _text(app, MENTIONS_ID) == ""

        line.value = ""
        await pilot.press("up")
        await pilot.pause()

        assert line.value == "first message", "up must be the history again"


async def test_an_email_address_does_not_open_a_file_picker() -> None:
    app = _build_app(Session(events=stream(happy_turn()), on_files=lambda: MENTION_PATHS))
    async with app.run_test() as pilot:
        await pilot.pause()
        await _typed_into(app, pilot, "mail ask@main.py")

        assert _text(app, MENTIONS_ID) == ""


async def test_the_picker_closes_when_the_token_stops_being_a_mention() -> None:
    # `escape` is deliberately not a dismissal — it interrupts the turn. So the picker
    # has to close on its own when the mention is finished or deleted.
    app = _build_app(Session(events=stream(happy_turn()), on_files=lambda: MENTION_PATHS))
    async with app.run_test() as pilot:
        await pilot.pause()
        await _typed_into(app, pilot, "@app")
        assert _text(app, MENTIONS_ID) != ""

        await pilot.press("space")
        await pilot.pause()

        assert _text(app, MENTIONS_ID) == ""


async def test_enter_still_submits_a_message_containing_a_mention() -> None:
    # If enter took the selection instead, an `@` word could never be sent in one
    # keystroke.
    submitted: list[str] = []
    app = _build_app(
        Session(
            events=stream(happy_turn()),
            on_submit=submitted.append,
            on_files=lambda: MENTION_PATHS,
        )
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        await _typed_into(app, pilot, "@app")
        await pilot.press("enter")
        await pilot.pause()

        assert submitted == ["@app"]
        assert _text(app, MENTIONS_ID) == "", "the line it was completing has been sent"


async def test_with_no_file_seam_an_at_is_ordinary_text() -> None:
    # The demo and a replayed recording have no repo behind them.
    app = _build_app(Session(events=stream(happy_turn())))
    async with app.run_test() as pilot:
        await pilot.pause()
        line = await _typed_into(app, pilot, "@app")

        assert _text(app, MENTIONS_ID) == ""
        await pilot.press("tab")
        await pilot.pause()
        assert line.value == "@app", "tab must not eat the line when nothing is offered"


async def test_ordinary_typing_never_asks_the_orchestrator_for_the_file_list() -> None:
    """The seam is a cached tree walk. Asking it per keystroke of ordinary prose would
    make every session pay for a feature it is not using."""
    asked: list[int] = []

    def files() -> tuple[str, ...]:
        asked.append(1)
        return MENTION_PATHS

    app = _build_app(Session(events=stream(happy_turn()), on_files=files))
    async with app.run_test() as pilot:
        await pilot.pause()
        await _typed_into(app, pilot, "no mentions here at all")

        assert asked == []


# --------------------------------------------------------------------------- #
# multi-line paste, driven through the pilot
# --------------------------------------------------------------------------- #


async def _paste(app: object, pilot: Any, text: str) -> Any:
    """Deliver a paste the way Textual does: straight to the focused widget.

    Not through `pilot.press`. A bracketed paste never becomes key events — the
    parser buffers the whole run and posts one `events.Paste` — so pressing the
    characters would exercise a path that cannot happen.
    """
    from textual import events

    line = _input(app)
    line.focus()
    await pilot.pause()
    line.post_message(events.Paste(text))
    await pilot.pause()
    return line


async def test_a_multi_line_paste_keeps_every_line_it_was_given() -> None:
    """Textual's own handler is `event.text.splitlines()[0]`.

    Before this, pasting a forty-line traceback put one line in the box and dropped
    the rest with no warning: the person saw a short line, pressed enter, and the
    model answered a question nobody had asked.
    """
    submitted: list[str] = []
    app = _build_app(Session(events=stream(happy_turn()), on_submit=submitted.append))
    async with app.run_test() as pilot:
        line = await _paste(app, pilot, "first line\nsecond line\nthird line")

        assert line.value != "first line", "the base handler must not get the text"
        assert "3 lines" in line.value

        await pilot.press("enter")
        await pilot.pause()
        assert submitted == ["first line\nsecond line\nthird line"]


async def test_a_multi_line_paste_is_one_submission_not_several() -> None:
    # The newlines are inside a stashed value, so nothing can read them as enters.
    submitted: list[str] = []
    app = _build_app(Session(events=stream(happy_turn()), on_submit=submitted.append))
    async with app.run_test() as pilot:
        await _paste(app, pilot, "one\ntwo\nthree\nfour")
        await pilot.press("enter")
        await pilot.pause()

        assert len(submitted) == 1


async def test_a_single_line_paste_still_lands_in_the_line_as_text() -> None:
    # The common case must not grow a marker: what you pasted is what the box holds.
    submitted: list[str] = []
    app = _build_app(Session(events=stream(happy_turn()), on_submit=submitted.append))
    async with app.run_test() as pilot:
        line = await _paste(app, pilot, "just one line")

        assert line.value == "just one line"
        await pilot.press("enter")
        await pilot.pause()
        assert submitted == ["just one line"]


async def test_a_paste_lands_where_the_cursor_is_and_keeps_the_typing_around_it() -> None:
    # Pasting into a half-written sentence is the ordinary way this gets used.
    submitted: list[str] = []
    app = _build_app(Session(events=stream(happy_turn()), on_submit=submitted.append))
    async with app.run_test() as pilot:
        await pilot.pause()
        await _typed_into(app, pilot, "explain ")
        await _paste(app, pilot, "a\nb")
        await _typed_into(app, pilot, " please")

        await pilot.press("enter")
        await pilot.pause()
        assert submitted == ["explain a\nb please"]


async def test_history_recalls_the_pasted_text_not_the_marker() -> None:
    """The book is cleared with the line, so a recalled marker would name a paste
    nobody is holding — and would reach the model as its own literal text."""
    submitted: list[str] = []
    app = _build_app(Session(events=stream(happy_turn()), on_submit=submitted.append))
    async with app.run_test() as pilot:
        await _paste(app, pilot, "alpha\nbeta")
        await pilot.press("enter")
        await pilot.pause()

        await pilot.press("up")
        await pilot.pause()
        assert _input(app).value == "alpha\nbeta"


async def test_the_stash_does_not_leak_into_the_next_prompt() -> None:
    # Cleared with the line it belonged to. A marker typed later is just text.
    submitted: list[str] = []
    app = _build_app(Session(events=stream(happy_turn()), on_submit=submitted.append))
    async with app.run_test() as pilot:
        line = await _paste(app, pilot, "a\nb")
        marker = line.value
        await pilot.press("enter")
        await pilot.pause()

        await _typed_into(app, pilot, marker)
        await pilot.press("enter")
        await pilot.pause()
        assert submitted[-1] == marker
