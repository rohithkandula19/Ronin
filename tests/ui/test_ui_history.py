"""Walking the prompt history, as pure logic.

The behaviour worth defending is not "up shows the last prompt" — it is that a
half-written line survives the walk. Someone typing ``fix the flaky t`` who presses up
to check what they asked earlier, then down to come back, must get their own words
returned. A history that loses them is one people stop touching, which is worse than
having none: the keystroke is still there, still tempting, and still destructive.

Pure by construction, so every case here runs without a terminal. The widget's job is
only to hand the current text in and put the returned text back; the Textual-driven
test in ``test_ui_textual.py`` covers that half.
"""

from __future__ import annotations

import pytest

from ronin.ui.reduce import (
    HISTORY_LIMIT,
    History,
    remember,
    walk_back,
    walk_forward,
)


def seeded(*prompts: str) -> History:
    history = History()
    for prompt in prompts:
        history = remember(history, prompt)
    return history


# --------------------------------------------------------------------------- #
# what gets recorded
# --------------------------------------------------------------------------- #


def test_prompts_are_recorded_oldest_first() -> None:
    assert seeded("one", "two", "three").entries == ("one", "two", "three")


@pytest.mark.parametrize("blank", ["", "   ", "\n", "\t "])
def test_a_blank_submission_is_not_a_prompt(blank: str) -> None:
    """The input line drops blank submits before dispatching, so recording one would
    put an entry in history that never ran."""
    assert remember(seeded("real"), blank).entries == ("real",)


def test_surrounding_whitespace_is_not_part_of_the_prompt() -> None:
    assert remember(History(), "  spaced out  ").entries == ("spaced out",)


def test_the_same_prompt_twice_in_a_row_is_recorded_once() -> None:
    """Holding enter, or re-sending the same thing, must not make ``up`` walk
    through duplicates of it."""
    assert seeded("again", "again", "again").entries == ("again",)


def test_a_repeat_after_something_else_is_a_real_step() -> None:
    """Only *consecutive* duplicates collapse. ``ls`` → ``fix it`` → ``ls`` is three
    things the user did, and flattening it would misreport the sequence."""
    assert seeded("ls", "fix it", "ls").entries == ("ls", "fix it", "ls")


def test_history_is_bounded_and_keeps_the_newest() -> None:
    """Held for the life of the process, so it cannot grow without limit — and when
    something has to go it is the oldest, which is the least likely to be wanted."""
    history = seeded(*(f"prompt {n}" for n in range(HISTORY_LIMIT + 25)))

    assert len(history.entries) == HISTORY_LIMIT
    assert history.entries[-1] == f"prompt {HISTORY_LIMIT + 24}"
    assert "prompt 0" not in history.entries


def test_submitting_ends_browsing() -> None:
    """The text is on its way, so there is no draft left to restore."""
    browsing, _ = walk_back(seeded("older"), "a draft")
    assert browsing.browsing

    after = remember(browsing, "sent")

    assert not after.browsing
    assert after.draft == ""


# --------------------------------------------------------------------------- #
# the draft — the case that makes history safe to use
# --------------------------------------------------------------------------- #


def test_the_draft_comes_back_after_walking_up_and_down_again() -> None:
    """The whole point. Up stashes what was typed; down past the newest restores it."""
    history = seeded("first", "second")

    history, shown = walk_back(history, "half written")
    assert shown == "second"
    history, shown = walk_back(history, "ignored")
    assert shown == "first"
    history, shown = walk_forward(history)
    assert shown == "second"
    history, shown = walk_forward(history)

    assert shown == "half written", "the user's own line did not come back"
    assert not history.browsing


def test_only_the_first_step_captures_the_draft() -> None:
    """Later steps pass *through* recalled prompts, and must not adopt one as the draft.

    If every step re-stashed, up-up-down-down would return the prompt walked through
    rather than what the user was writing — which is the failure that looks like
    history "eating" your text.
    """
    history = seeded("a", "b", "c")

    history, _ = walk_back(history, "mine")
    history, _ = walk_back(history, "b")  # what the box holds now, not a new draft
    history, _ = walk_back(history, "a")

    assert history.draft == "mine"


def test_an_empty_draft_is_restored_as_empty_not_skipped() -> None:
    """Someone who pressed up on an empty line gets an empty line back, not the
    newest prompt left behind."""
    history = seeded("only")

    history, _ = walk_back(history, "")
    history, shown = walk_forward(history)

    assert shown == ""
    assert not history.browsing


# --------------------------------------------------------------------------- #
# the edges of the walk
# --------------------------------------------------------------------------- #


def test_up_with_no_history_does_nothing() -> None:
    """``None`` means "nothing moved", which the widget renders as leaving the box
    alone. Returning an empty string instead would clear whatever was typed."""
    history, shown = walk_back(History(), "typing")

    assert shown is None
    assert not history.browsing


def test_up_at_the_oldest_prompt_holds_rather_than_wrapping() -> None:
    """Wrapping to the newest would make a held-down key cycle forever with no way to
    tell where you are."""
    history = seeded("oldest", "newest")

    history, _ = walk_back(history, "")
    history, _ = walk_back(history, "")
    held, shown = walk_back(history, "")

    assert shown is None
    assert held.cursor == 0


def test_down_when_not_browsing_does_nothing() -> None:
    assert walk_forward(seeded("one"))[1] is None


def test_down_past_the_draft_stays_there() -> None:
    history = seeded("one")

    history, _ = walk_back(history, "draft")
    history, _ = walk_forward(history)
    history, shown = walk_forward(history)

    assert shown is None, "there is nothing newer than the line you are typing"


# --------------------------------------------------------------------------- #
# browsing never rewrites what was submitted
# --------------------------------------------------------------------------- #


def test_walking_does_not_change_the_entries() -> None:
    """Frozen and rebuilt, so a recalled prompt cannot be edited into history by
    walking past it."""
    original = seeded("one", "two")

    walked, _ = walk_back(original, "x")
    walked, _ = walk_back(walked, "an edit of two")

    assert walked.entries == original.entries == ("one", "two")
