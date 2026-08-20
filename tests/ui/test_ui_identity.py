"""Identity: the wordmark, and the dialect a terminal that wants no colour gets.

Two gaps the identity audit found in the v2 tree, and one non-gap worth recording.

The non-gap first, because it shaped the rest: **colour is already single-sourced.**
Every renderer takes an injected ``Styles``, the token vocabulary is closed and
validated, and there are exactly three definition sites in one fifty-line block of
``render.py``. There was nothing to centralize, so nothing here does.

The gaps:

* **Nothing identified the program.** The line session printed one sentence; the
  Textual app's ``compose`` yielded only empty widgets, so ``ronin`` on a tty opened
  a blank screen — which reads as "nothing loaded", not "ready".
* **There was no way to turn colour off.** The app hardcoded console markup. Plain
  is not the answer on an in-band surface: dropping the escape along with the colour
  would let a diff line reading ``[dim]`` be eaten by the host's markup parser.

The wordmark is deliberately restrained. ``packages/design-system`` states the house
aesthetic as ink on paper, "the deliberate antithesis of the neon-gradient AI
aesthetic", so a heavy filled banner would be the thing it is the antithesis of.
"""

from __future__ import annotations

import pytest

from ronin.cli.main import REPL_HINT, TUI_HINT, repl_banner, wants_colour
from ronin.ui.render import (
    ANSI,
    COMPACT_WORDMARK,
    MARKUP,
    NO_COLOUR_MARKUP,
    PLAIN,
    TAGLINE,
    WORDMARK,
    WORDMARK_WIDTH,
    escape_markup,
    render_banner,
)

# --------------------------------------------------------------------------- #
# the wordmark
# --------------------------------------------------------------------------- #


def test_the_banner_shows_the_wordmark_and_says_what_this_is() -> None:
    out = render_banner()
    for line in WORDMARK:
        assert line in out
    assert TAGLINE in out


def test_the_version_is_reported_rather_than_invented() -> None:
    # A banner that prints a number nobody supplied is a banner that lies after the
    # next release.
    assert "v9.9.9" in render_banner(version="v9.9.9")
    assert "None" not in render_banner()
    assert "v" not in render_banner().replace("terminal-native", "").replace("masterless", "")


def test_a_hint_is_shown_when_given_and_absent_when_not() -> None:
    assert "/help" in render_banner(hint="/help for commands")
    assert "/help" not in render_banner()


def test_a_narrow_terminal_gets_the_compact_form_rather_than_a_wrapped_one() -> None:
    """A wordmark that wraps is worse than no wordmark."""
    narrow = render_banner(width=WORDMARK_WIDTH - 1)
    assert COMPACT_WORDMARK in narrow
    assert WORDMARK[0] not in narrow
    assert len(narrow.splitlines()) == 1


def test_a_wide_terminal_gets_the_full_wordmark() -> None:
    wide = render_banner(width=200)
    assert WORDMARK[0] in wide


def test_width_zero_means_unconstrained() -> None:
    # The default, and what a caller that does not know the terminal size passes.
    assert render_banner(width=0) == render_banner()


def test_every_wordmark_line_is_the_same_width() -> None:
    # A ragged block wordmark reads as broken rather than as a logo.
    assert len({len(line) for line in WORDMARK}) == 1


def test_the_wordmark_renders_with_no_colour_at_all() -> None:
    # It has to work on a pipe, in CI logs, and for anyone who set NO_COLOR.
    out = render_banner(styles=PLAIN)
    assert "\x1b" not in out
    assert WORDMARK[0] in out


def test_the_banner_is_pure_and_emits_no_escapes_of_its_own() -> None:
    # Colour comes only from the injected map, like every other renderer.
    assert "\x1b" not in render_banner(hint="h", version="v")
    assert "\x1b" in render_banner(hint="h", styles=ANSI)


# --------------------------------------------------------------------------- #
# no colour, without losing the escaping
# --------------------------------------------------------------------------- #


def test_the_no_colour_dialect_applies_no_style() -> None:
    assert NO_COLOUR_MARKUP.wrap("added", "+x") == "+x"
    assert NO_COLOUR_MARKUP.wrap("danger", "rm -rf /") == "rm -rf /"


def test_the_no_colour_dialect_still_escapes_markup() -> None:
    """The reason it is not simply ``PLAIN``.

    The app renders in band. A tool result containing ``[dim]`` must be escaped
    whether or not anything is coloured, or the host's parser eats it — "no colour"
    would silently become "no colour and also corrupted text".
    """
    assert NO_COLOUR_MARKUP.text("[dim]") == escape_markup("[dim]")
    assert PLAIN.text("[dim]") == "[dim]"


def test_the_no_colour_dialect_still_strips_control_characters() -> None:
    assert "\x1b" not in NO_COLOUR_MARKUP.text("a \x1b]0;PWNED\x07 b")


def test_the_no_colour_dialect_carries_no_pairs_that_could_drift() -> None:
    # Defined as "MARKUP minus the colour", so it cannot fall behind when a token is
    # added to MARKUP: it has no pairs at all.
    assert NO_COLOUR_MARKUP.pairs == {}
    assert MARKUP.pairs != {}


# --------------------------------------------------------------------------- #
# the decision is made at the binding site, not in the pure layer
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("value", ["1", "0", "true", "false", "anything"])
def test_no_color_set_to_anything_means_no_colour(value: str) -> None:
    # no-color.org: presence is the signal and the value is not parsed. `NO_COLOR=0`
    # still means no colour, because a user who set it at all meant it.
    assert not wants_colour({"NO_COLOR": value})


def test_an_empty_or_absent_no_color_leaves_colour_on() -> None:
    assert wants_colour({})
    assert wants_colour({"NO_COLOR": ""})


def test_the_line_session_greeting_names_how_to_get_help_and_leave() -> None:
    out = repl_banner(version="v1.2.3")
    assert "/help" in out
    assert "exit" in out
    assert "v1.2.3" in out


def test_the_line_session_greeting_never_emits_an_escape() -> None:
    """``tests/ui/test_ui_controls`` forbids any escape on the line path."""
    assert "\x1b" not in repl_banner(version="v1.2.3")


def test_the_two_hints_say_different_things_because_the_surfaces_differ() -> None:
    # The line session needs to say how to leave; the TUI needs to name its keys.
    assert REPL_HINT != TUI_HINT
    assert "esc" in TUI_HINT
    assert "exit" in REPL_HINT


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__])
