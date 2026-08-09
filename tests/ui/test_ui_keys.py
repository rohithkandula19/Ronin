"""The two hard key behaviours, as pure logic: the mode cycle and esc esc."""
from __future__ import annotations

import pytest

from ronin.core.types import Mode
from ronin.ui.reduce import (
    DOUBLE_ESCAPE_WINDOW_SECONDS,
    MODE_CYCLE,
    EscapeAction,
    EscapeState,
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
