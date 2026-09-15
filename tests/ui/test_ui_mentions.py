"""``@file`` mentions: what counts as one, what a query returns, and what tab inserts.

The corpus here is ten hand-written paths rather than a repo, which is the point of
:mod:`ronin.ui.mentions` being pure: the ranking is asserted exactly, so "it depends on
the walk order" cannot hide in it.

Two properties get the most attention, because both are the difference between a
feature and an irritation: an ``@`` that is *not* at the start of a token is not a
mention (an email address must not open a file picker), and ranking is a total order
(the same query always produces the same list, in the same order).
"""

from __future__ import annotations

import pytest

from ronin.ui.mentions import (
    MENTION_LIMIT,
    NO_COMPLETION,
    Completion,
    accept,
    active_mention,
    rank,
)
from ronin.ui.reduce import ViewState
from ronin.ui.render import MARKUP, PLAIN, render_completion

#: A corpus shaped like a real repo: the same basename in two places, a name that is a
#: prefix of another, and files with no extension.
PATHS = (
    "README.md",
    "apps/api/main.py",
    "docs/ARCHITECTURE.md",
    "pyproject.toml",
    "src/ronin/cli/main.py",
    "src/ronin/context/compaction.py",
    "src/ronin/ui/app.py",
    "src/ronin/ui/render.py",
    "tests/ui/test_ui_app.py",
    "tests/ui/test_ui_render.py",
)


# --------------------------------------------------------------------------- #
# What is a mention
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("text", "cursor", "query"),
    [
        ("@app", 4, "app"),
        ("look at @app", 12, "app"),
        ("@", 1, ""),
        ("look at @app.py now", 12, "app.py"),  # cursor mid-token, token continues
        ("  @app", 6, "app"),
        ("@app\tand more", 4, "app"),
    ],
)
def test_a_token_starting_with_at_is_a_mention(text: str, cursor: int, query: str) -> None:
    mention = active_mention(text, cursor)
    assert mention is not None
    assert mention.query == query


@pytest.mark.parametrize(
    ("text", "cursor"),
    [
        ("ask@example.com", 15),  # an email is not a file picker
        ("git@github.com:me/repo", 22),
        ("user@host", 9),
        ("plain text", 5),
        ("", 0),
        ("look at @app now", 16),  # cursor is past the mention, in another token
    ],
)
def test_an_at_inside_a_word_is_part_of_the_word(text: str, cursor: int) -> None:
    assert active_mention(text, cursor) is None


def test_the_mention_spans_the_whole_token_not_just_up_to_the_cursor() -> None:
    # Editing the middle of an already-typed path: replacing only to the cursor would
    # leave the tail behind as garbage.
    mention = active_mention("see @src/ronin done", 8)
    assert mention is not None
    assert (mention.start, mention.end) == (4, 14)


def test_a_cursor_outside_the_text_is_not_a_mention() -> None:
    assert active_mention("@app", 99) is None
    assert active_mention("@app", -1) is None


# --------------------------------------------------------------------------- #
# Ranking
# --------------------------------------------------------------------------- #


def test_a_name_match_beats_a_path_match() -> None:
    # Someone typing "app" wants ui/app.py, not apps/api/main.py — and the reason is
    # exactly that the first is a name match and the second only a path match.
    ordered = rank("app", PATHS)
    assert ordered[0] == "src/ronin/ui/app.py"
    assert ordered.index("src/ronin/ui/app.py") < ordered.index("apps/api/main.py")


def test_a_name_prefix_beats_a_name_substring() -> None:
    ordered = rank("test_ui_app", PATHS)
    assert ordered[0] == "tests/ui/test_ui_app.py"


def test_the_shorter_path_wins_inside_a_rung() -> None:
    # Two files called main.py; neither is a better name match, so the tie breaks on
    # length. Deterministic beats clever.
    ordered = rank("main.py", PATHS)
    assert ordered[:2] == ("apps/api/main.py", "src/ronin/cli/main.py")


def test_matching_is_case_insensitive() -> None:
    assert rank("readme", PATHS)[0] == "README.md"
    assert rank("ARCH", PATHS)[0] == "docs/ARCHITECTURE.md"


def test_characters_in_order_are_enough() -> None:
    # The whole point of fuzzy: you remember roughly what it is called.
    assert "src/ronin/context/compaction.py" in rank("cmpct", PATHS)
    assert "src/ronin/context/compaction.py" in rank("ctx/comp", PATHS)


def test_characters_out_of_order_are_not_a_match() -> None:
    assert rank("ppa.yp", PATHS) == ()


def test_the_same_query_always_gives_the_same_order() -> None:
    # A total order, so the list cannot depend on dict or walk order. Asserted against
    # a reversed corpus, which is the only way to catch an accidental dependence.
    assert rank("main", PATHS) == rank("main", tuple(reversed(PATHS)))


def test_a_bare_at_offers_nothing() -> None:
    # Ranking 3000 paths against "" can only return whatever sorts first, which teaches
    # the user the feature is broken rather than that it is waiting for a character.
    assert rank("", PATHS) == ()


def test_the_list_is_capped_so_it_cannot_take_the_screen() -> None:
    many = tuple(f"src/thing{n}.py" for n in range(50))
    assert len(rank("thing", many)) == MENTION_LIMIT
    assert len(rank("thing", many, limit=3)) == 3


def test_nothing_matches_rather_than_everything_matching() -> None:
    assert rank("zzzz", PATHS) == ()


# --------------------------------------------------------------------------- #
# The offered list, and taking from it
# --------------------------------------------------------------------------- #


def test_nothing_offered_is_closed_and_chooses_nothing() -> None:
    assert NO_COMPLETION.open is False
    assert NO_COMPLETION.choice == ""
    assert NO_COMPLETION.moved(1) is NO_COMPLETION


def test_the_selection_wraps_at_both_ends() -> None:
    # Wrapping, not clamping: a wrap costs one keypress to undo, where a clamp leaves
    # `up` silently doing nothing at the top, which reads as a wedged UI.
    offered = Completion(candidates=("a", "b", "c"))
    assert offered.choice == "a"
    assert offered.moved(-1).choice == "c"
    assert offered.moved(1).moved(1).moved(1).choice == "a"


def test_taking_a_path_replaces_the_mention_and_leaves_one_space() -> None:
    text, cursor = accept("look at @app", 12, "src/ronin/ui/app.py")
    assert text == "look at src/ronin/ui/app.py "
    assert cursor == len(text)


def test_the_space_is_not_doubled_and_the_cursor_lands_past_it() -> None:
    # The cursor must sit where the next word goes. Left in front of the existing
    # space it would make the next character butt straight against the path:
    # "src/ronin/ui/app.pyand fix it".
    text, cursor = accept("see @app and fix it", 8, "src/ronin/ui/app.py")
    assert text == "see src/ronin/ui/app.py and fix it"
    assert text[cursor:] == "and fix it"


def test_taking_replaces_the_whole_token_not_just_up_to_the_cursor() -> None:
    text, _cursor = accept("see @src/ron done", 12, "src/ronin/ui/app.py")
    assert text == "see src/ronin/ui/app.py done"


def test_a_stray_tab_outside_a_mention_cannot_corrupt_the_line() -> None:
    assert accept("plain text", 5, "src/ronin/ui/app.py") == ("plain text", 5)
    assert accept("@app", 4, "") == ("@app", 4)


# --------------------------------------------------------------------------- #
# What the picker looks like
# --------------------------------------------------------------------------- #


def test_nothing_offered_renders_nothing_at_all() -> None:
    assert render_completion(ViewState(), styles=PLAIN) == ""


def test_the_selected_path_is_marked_and_the_keys_are_named() -> None:
    state = ViewState().with_completion(Completion(candidates=("a.py", "b.py"), selected=1))
    shown = render_completion(state, styles=PLAIN)
    lines = shown.splitlines()

    assert "tab" in lines[0] and "up/down" in lines[0]
    assert lines[1].strip() == "a.py"
    assert lines[2].strip() == "> b.py"


def test_a_path_containing_markup_renders_as_its_name() -> None:
    # A repo is allowed to contain a file with a bracket in its name — every Next.js
    # route does — and the approval prompt already taught this the expensive way:
    # `[y]es` rendered as `es`. Asserted on what a terminal would actually show, since
    # that is the only form in which the bug is visible.
    state = ViewState().with_completion(Completion(candidates=("src/[id]/page.tsx",)))

    assert _resolved(render_completion(state, styles=MARKUP)).endswith("src/[id]/page.tsx")


def _resolved(markup: str) -> str:
    """What a terminal would actually show for ``markup``."""
    from rich.markup import render

    return str(render(markup))


def test_the_offered_list_is_identity_preserving_when_it_did_not_move() -> None:
    # Recomputed on every keystroke of a mention, so an unchanged list must not rebuild
    # the state.
    offered = Completion(candidates=("a.py",))
    state = ViewState().with_completion(offered)
    assert state.with_completion(Completion(candidates=("a.py",))) is state
