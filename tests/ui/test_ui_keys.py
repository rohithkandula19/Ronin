"""The two hard key behaviours, as pure logic: the mode cycle and esc esc."""

from __future__ import annotations

import pytest

from ronin.core.types import Mode
from ronin.ui.reduce import (
    APPROVAL_KEYS,
    DOUBLE_ESCAPE_WINDOW_SECONDS,
    MODE_CYCLE,
    REASON_KEY,
    REMEMBER_KEY,
    EscapeAction,
    EscapeState,
    decision_for,
    deny_with,
    mode_label,
    next_mode,
    press_escape,
)


def test_the_cycle_is_normal_then_auto_accept_then_plan() -> None:
    assert MODE_CYCLE == (Mode.ASK, Mode.AUTO_EDIT, Mode.PLAN)
    assert [mode_label(mode) for mode in MODE_CYCLE] == ["normal", "auto-accept", "plan"]


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (Mode.ASK, Mode.AUTO_EDIT),
        (Mode.AUTO_EDIT, Mode.PLAN),
        (Mode.PLAN, Mode.ASK),
    ],
)
def test_shift_tab_walks_the_cycle(mode: Mode, expected: Mode) -> None:
    assert next_mode(mode) is expected


def test_three_presses_return_to_the_start() -> None:
    mode = Mode.ASK
    for _ in range(len(MODE_CYCLE)):
        mode = next_mode(mode)
    assert mode is Mode.ASK


def test_cycling_out_of_a_mode_granted_out_of_band_cannot_widen_it() -> None:
    # FULL is not in the cycle; a keystroke must never hand out more than the
    # cycle's most restrictive member.
    assert next_mode(Mode.FULL) is MODE_CYCLE[0]
    assert MODE_CYCLE[0].at_least(Mode.FULL) is False


def test_every_mode_has_a_label() -> None:
    for mode in Mode:
        assert mode_label(mode)


def test_the_first_escape_interrupts_immediately() -> None:
    state, action = press_escape(EscapeState(), now=10.0)
    assert action is EscapeAction.INTERRUPT
    assert state.last_press == 10.0


def test_a_second_escape_inside_the_window_rewinds() -> None:
    first, _ = press_escape(EscapeState(), now=10.0)
    second, action = press_escape(first, now=10.0 + DOUBLE_ESCAPE_WINDOW_SECONDS / 2)
    assert action is EscapeAction.REWIND
    assert second.last_press is None


def test_a_press_exactly_on_the_window_boundary_still_rewinds() -> None:
    first, _ = press_escape(EscapeState(), now=0.0)
    _, action = press_escape(first, now=DOUBLE_ESCAPE_WINDOW_SECONDS)
    assert action is EscapeAction.REWIND


def test_a_second_escape_after_the_window_interrupts_again() -> None:
    first, _ = press_escape(EscapeState(), now=0.0)
    state, action = press_escape(first, now=DOUBLE_ESCAPE_WINDOW_SECONDS + 0.01)
    assert action is EscapeAction.INTERRUPT
    assert state.last_press == DOUBLE_ESCAPE_WINDOW_SECONDS + 0.01


def test_a_third_quick_press_starts_a_fresh_sequence_rather_than_rewinding_twice() -> None:
    state, first = press_escape(EscapeState(), now=0.0)
    state, second = press_escape(state, now=0.1)
    state, third = press_escape(state, now=0.2)
    assert (first, second, third) == (
        EscapeAction.INTERRUPT,
        EscapeAction.REWIND,
        EscapeAction.INTERRUPT,
    )


def test_a_clock_that_moves_backwards_is_a_new_sequence_not_a_double_press() -> None:
    first, _ = press_escape(EscapeState(), now=100.0)
    _, action = press_escape(first, now=99.0)
    assert action is EscapeAction.INTERRUPT


def test_the_window_is_a_named_constant_a_caller_can_override() -> None:
    first, _ = press_escape(EscapeState(), now=0.0, window=0.0)
    _, action = press_escape(first, now=0.001, window=0.0)
    assert action is EscapeAction.INTERRUPT


# --------------------------------------------------------------------------- #
# answering an approval
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("key", "approved", "remember"),
    [("y", True, False), ("a", True, True), ("n", False, False), ("escape", False, False)],
)
def test_each_answering_key_means_exactly_one_thing(
    key: str, approved: bool, remember: bool
) -> None:
    """The most consequential table in the UI: which keystrokes let an edit run.

    Tested here rather than only through the widget because this is the layer that
    decides, and a table is exhaustively checkable in a way a terminal is not.
    """
    decision = decision_for(key)
    assert decision is not None
    assert (decision.approved, decision.remember) == (approved, remember)


@pytest.mark.parametrize("key", ["", "j", "Y", "enter", "space", "ctrl+c", "tab", "yes"])
def test_no_other_key_answers_anything(key: str) -> None:
    """``None`` means "not an answer", and that is different from "no".

    ``enter`` and ``space`` are in this list deliberately: they are what a held-down or
    stray keypress produces while a dialog appears, and either one approving would be an
    edit nobody agreed to. Capital ``Y`` is here because the modal does not lowercase —
    a key that looks like a yes but is not mapped must do nothing rather than nearly work.
    """
    assert decision_for(key) is None


def test_only_two_keys_can_approve_and_they_are_named() -> None:
    """Reads the table itself, so adding a third approving key without arguing for it in
    a test fails here. The count is the claim: two ways to say yes, no more."""
    approving = sorted(key for key, approves in APPROVAL_KEYS.items() if approves)
    assert approving == ["a", "y"]
    assert REMEMBER_KEY in approving
    assert APPROVAL_KEYS["escape"] is False, "escape must never be an approval"


def test_the_reason_key_is_not_an_answer_on_its_own() -> None:
    """It opens a line to type in; it decides nothing.

    Kept out of :data:`APPROVAL_KEYS` deliberately, so ``decision_for`` keeps returning
    ``None`` for it and the request stays standing. If it resolved here it would be a
    second denial key wearing a confusing name, and the human would lose the chance to
    back out of a keystroke they took back.
    """
    assert REASON_KEY not in APPROVAL_KEYS
    assert decision_for(REASON_KEY) is None


def test_the_reason_key_does_not_collide_with_an_answering_key() -> None:
    """A letter that both denies instantly and opens a prompt cannot do either well."""
    assert REASON_KEY not in set(APPROVAL_KEYS)
    assert REASON_KEY != REMEMBER_KEY


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("use staging", "use staging"),
        ("  use staging  ", "use staging"),
        ("", ""),
        ("   ", ""),
        ("\n\t", ""),
    ],
)
def test_deny_with_carries_the_words_and_leaves_blank_blank(typed: str, expected: str) -> None:
    """Whitespace is trimmed; emptiness is preserved rather than papered over.

    A blank reason must stay blank so the engine's own no-reason wording fires.
    A placeholder here would render, through ``PolicyEngine._denial``, as "the user
    declined and said: the user declined this action" — doubled back on itself.
    """
    decision = deny_with(typed)
    assert decision.approved is False
    assert decision.reason == expected
    assert not decision.remember, "a denial has nothing to remember"


@pytest.mark.parametrize("key", ["n", "escape"])
def test_a_bare_denial_carries_no_words_of_its_own(key: str) -> None:
    """The engine owns every word of a denial, so this layer supplies none.

    ``reason`` travels to the policy engine as ``Answer.feedback``, which the engine
    reproduces as *the human's own words*. A placeholder here was quoted back as though
    the human had typed it — and took the branch that says "Take that as a correction,
    not a dead end", inviting the retry a bare ``n`` exists to stop. What the model is
    actually told is asserted at the policy layer, in ``tests/safety``.
    """
    denied = decision_for(key)
    assert denied is not None
    assert denied.approved is False
    assert denied.reason == ""
    assert not denied.remember, "a denial has nothing to remember"
