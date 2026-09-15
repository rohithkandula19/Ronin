"""Stashing a multi-line paste, and putting it back on submit.

Pure: a `PasteBook` is a value and nothing here needs a terminal. The widget half —
that Textual's own handler is bypassed, and that the expanded text is what reaches
`on_submit` — is driven through the pilot in `test_ui_textual.py`.
"""

from __future__ import annotations

from ronin.ui.paste import (
    NO_PASTES,
    PASTE_THRESHOLD_LINES,
    PasteBook,
    expand,
    stash,
    token_for,
)


def test_a_single_line_paste_goes_straight_into_the_line() -> None:
    # A one-line paste *is* what the box holds. Hiding it behind a marker would make
    # the common case worse to serve the rare one.
    book, inserted = stash(NO_PASTES, "just one line")
    assert inserted == "just one line"
    assert book is NO_PASTES


def test_a_multi_line_paste_is_held_and_a_marker_takes_its_place() -> None:
    """The bug this exists for: Textual keeps `event.text.splitlines()[0]` and drops
    the rest with no sign anything happened."""
    book, inserted = stash(NO_PASTES, "one\ntwo\nthree")
    assert inserted == token_for(1, 3)
    assert len(book.items) == 1
    assert expand(inserted, book) == "one\ntwo\nthree"


def test_the_marker_says_how_much_it_is_holding() -> None:
    # It is the only thing on screen before enter is pressed, so it has to be enough
    # to tell someone that nothing was lost.
    _book, inserted = stash(NO_PASTES, "\n".join(str(n) for n in range(42)))
    assert "42" in inserted


def test_the_threshold_is_where_the_marker_starts() -> None:
    exactly = "\n".join(["x"] * PASTE_THRESHOLD_LINES)
    below = "\n".join(["x"] * (PASTE_THRESHOLD_LINES - 1))
    assert stash(NO_PASTES, exactly)[1] != exactly
    assert stash(NO_PASTES, below)[1] == below


def test_several_pastes_are_held_at_once_and_each_expands_in_place() -> None:
    # Someone assembling a prompt from two files pastes twice before sending once.
    book, first = stash(NO_PASTES, "a\nb")
    book, second = stash(book, "c\nd")
    assert first != second
    assert expand(f"compare {first} against {second}", book) == "compare a\nb against c\nd"


def test_a_paste_is_put_back_exactly_as_it_arrived() -> None:
    """Byte-for-byte, including the whitespace. A traceback whose indentation is
    reflowed on the way through is a traceback the model reads differently."""
    original = "Traceback:\n  File 'a.py', line 3\n    raise ValueError\n\nValueError\n"
    book, marker = stash(NO_PASTES, original)
    assert expand(marker, book) == original


def test_carriage_returns_survive_the_round_trip() -> None:
    # Pasting from a Windows editor or a browser is the ordinary case, not the exotic
    # one, and `\r\n` that becomes `\n` is a silently different file.
    original = "line one\r\nline two\r\n"
    book, marker = stash(NO_PASTES, original)
    assert expand(marker, book) == original


def test_a_marker_this_book_never_issued_is_left_alone() -> None:
    """Someone can type square brackets. Rewriting words a person actually wrote is a
    worse failure than showing them a stray marker, so an unknown one passes through."""
    assert expand(token_for(9, 9), NO_PASTES) == token_for(9, 9)
    book, _marker = stash(NO_PASTES, "a\nb")
    typed = f"what does {token_for(7, 3)} mean"
    assert expand(typed, book) == typed


def test_a_half_deleted_marker_expands_to_nothing() -> None:
    # Backspacing into a marker is easy to do by accident. The remains are text, and
    # text is all they become.
    book, marker = stash(NO_PASTES, "a\nb")
    broken = marker[:-1]
    assert expand(broken, book) == broken


def test_a_marker_can_be_deleted_to_drop_the_paste() -> None:
    # Deleting the marker is how you change your mind about a paste, so the text must
    # not come back by some other route.
    book, _marker = stash(NO_PASTES, "a\nb")
    assert expand("never mind", book) == "never mind"


def test_the_same_paste_twice_expands_at_both_markers() -> None:
    # `str.replace` is global by design: the marker is a stand-in for the text, and a
    # stand-in that only works in the first position is a trap.
    book, marker = stash(NO_PASTES, "x\ny")
    assert expand(f"{marker} and {marker}", book) == "x\ny and x\ny"


def test_an_empty_book_changes_nothing() -> None:
    assert expand("plain prose", NO_PASTES) == "plain prose"
    assert NO_PASTES.lines == 0


def test_the_book_reports_how_many_lines_it_is_holding() -> None:
    # What a "2 pastes attached (5 lines)" notice would count.
    book, _ = stash(NO_PASTES, "a\nb\nc")
    book, _ = stash(book, "d\ne")
    assert book.lines == 5


def test_a_book_is_a_value_and_stashing_does_not_mutate_the_old_one() -> None:
    # Held on the `KeyController` beside the history, and for the same reason: state
    # that is replaced rather than edited cannot be changed by something holding it.
    first = PasteBook()
    second, _marker = stash(first, "a\nb")
    assert first.items == ()
    assert len(second.items) == 1
