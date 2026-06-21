"""Tests for hunk-by-hunk diff review (``ronin_cli.hunk_review``).

Covers the pure core:
  * ``split_hunks`` count + header parity with ``difflib.unified_diff`` on a
    known multi-change diff;
  * the round-trip LAWS — select ALL → ``new``, select NONE → ``old``, a chosen
    subset applies exactly that subset — including files WITHOUT a trailing
    newline, pure-insert, and pure-delete cases;
  * a randomized fuzz that the laws hold for arbitrary edits.

``review`` (the one impure function) is checked only for its SAFETY contract:
no hunks / non-interactive stdin → return ``new`` unchanged.
"""
from __future__ import annotations

import difflib
import random

import pytest

from ronin_cli.hunk_review import Hunk, apply_hunks, render_hunk, review, split_hunks


def _all(hunks: list[Hunk]) -> set[int]:
    return set(range(len(hunks)))


def _headers(old: str, new: str) -> list[str]:
    return [
        line.rstrip("\n")
        for line in difflib.unified_diff(
            old.splitlines(keepends=True), new.splitlines(keepends=True), n=3
        )
        if line.startswith("@@")
    ]


# --------------------------------------------------------------------------
# split_hunks: count + header parity
# --------------------------------------------------------------------------

def test_split_hunks_single_change_count() -> None:
    old, new = "a\nb\nc\n", "a\nB\nc\n"
    hunks = split_hunks(old, new)
    assert len(hunks) == 1
    assert hunks[0].header == _headers(old, new)[0]


def test_split_hunks_two_distant_changes_make_two_hunks() -> None:
    # Two edits >2*context lines apart → difflib emits two separate hunks.
    n = 24
    old = "".join(f"L{i}\n" for i in range(n))
    new = old.replace("L1\n", "A\n").replace("L20\n", "B\n")
    hunks = split_hunks(old, new)
    assert len(hunks) == 2
    # headers must match what difflib.unified_diff would emit, in order
    assert [h.header for h in hunks] == _headers(old, new)


def test_split_hunks_close_changes_merge_into_one() -> None:
    # Edits within 2*context collapse into ONE hunk (matches difflib grouping).
    old = "".join(f"L{i}\n" for i in range(10))
    new = old.replace("L1\n", "A\n").replace("L5\n", "B\n")
    assert len(split_hunks(old, new)) == 1


def test_split_hunks_identical_yields_no_hunks() -> None:
    assert split_hunks("a\nb\n", "a\nb\n") == []


# --------------------------------------------------------------------------
# Round-trip LAWS
# --------------------------------------------------------------------------

# (old, new) pairs spanning the tricky shapes the prompt calls out.
_CASES = [
    ("a\nb\nc\n", "a\nB\nc\n"),                    # single in-place change
    ("a\nb\nc", "a\nB\nc"),                        # NO trailing newline
    ("a\nc\n", "a\nb\nc\n"),                       # pure insert
    ("a\nb\nc\n", "a\nc\n"),                       # pure delete
    ("a\nb\nc", "a\nb\nc\nd"),                     # append, no trailing newline
    ("", "hello\nworld\n"),                        # empty → content
    ("hello\nworld\n", ""),                        # content → empty (delete all)
    ("a\nb\nc\nd\ne\n", "z\nb\nc\nd\nq\n"),        # edits at both ends
    ("x\n", "x\n"),                                # identical
]


@pytest.mark.parametrize("old,new", _CASES)
def test_law_all_reproduces_new(old: str, new: str) -> None:
    hunks = split_hunks(old, new)
    assert apply_hunks(old, hunks, _all(hunks)) == new


@pytest.mark.parametrize("old,new", _CASES)
def test_law_none_reproduces_old(old: str, new: str) -> None:
    hunks = split_hunks(old, new)
    assert apply_hunks(old, hunks, set()) == old


def test_subset_applies_exactly_that_subset() -> None:
    # Three well-separated edits → three independent hunks.
    n = 40
    old = "".join(f"L{i}\n" for i in range(n))
    new = (
        old.replace("L2\n", "A\n")
        .replace("L20\n", "B\n")
        .replace("L38\n", "C\n")
    )
    hunks = split_hunks(old, new)
    assert len(hunks) == 3

    # Apply ONLY the middle hunk: B lands, A and C do not, everything else is old.
    only_mid = apply_hunks(old, hunks, {1})
    assert "B\n" in only_mid
    assert "A\n" not in only_mid and "C\n" not in only_mid
    assert "L2\n" in only_mid and "L38\n" in only_mid  # outer edits reverted to old
    # equivalent to new with the OTHER two changes rolled back
    assert only_mid == new.replace("A\n", "L2\n").replace("C\n", "L38\n")

    # Apply the two outer hunks: A and C land, B stays old.
    outer = apply_hunks(old, hunks, {0, 2})
    assert "A\n" in outer and "C\n" in outer and "B\n" not in outer
    assert outer == new.replace("B\n", "L20\n")


def test_subset_is_order_independent() -> None:
    # apply_hunks must sort by old position regardless of the set's iteration.
    n = 40
    old = "".join(f"L{i}\n" for i in range(n))
    new = old.replace("L3\n", "A\n").replace("L30\n", "B\n")
    hunks = split_hunks(old, new)
    assert len(hunks) == 2
    assert apply_hunks(old, hunks, {0, 1}) == apply_hunks(old, hunks, {1, 0}) == new


def test_pure_insert_subset() -> None:
    old = "a\nb\n"
    new = "a\nINSERT\nb\n"
    hunks = split_hunks(old, new)
    assert len(hunks) == 1
    assert apply_hunks(old, hunks, set()) == old      # rejecting keeps original
    assert apply_hunks(old, hunks, {0}) == new        # accepting inserts


def test_pure_delete_subset() -> None:
    old = "a\nGONE\nb\n"
    new = "a\nb\n"
    hunks = split_hunks(old, new)
    assert len(hunks) == 1
    assert apply_hunks(old, hunks, set()) == old      # rejecting keeps the line
    assert apply_hunks(old, hunks, {0}) == new        # accepting drops it


def test_no_trailing_newline_byte_exact() -> None:
    # The terminator (or its absence) must survive a full round trip exactly.
    old = "first\nsecond"          # no trailing \n
    new = "first\nSECOND"          # still no trailing \n
    hunks = split_hunks(old, new)
    assert apply_hunks(old, hunks, _all(hunks)) == new
    assert apply_hunks(old, hunks, set()) == old
    # rejecting must not sneak a newline onto the last line
    assert not apply_hunks(old, hunks, set()).endswith("\n")


# --------------------------------------------------------------------------
# Fuzz: the laws hold for arbitrary edits
# --------------------------------------------------------------------------

def test_laws_hold_under_fuzz() -> None:
    rng = random.Random(1234)
    for _ in range(400):
        n = rng.randint(0, 15)
        a = [f"line{i}\n" for i in range(n)]
        b: list[str] = []
        for ln in a:
            r = rng.random()
            if r < 0.6:
                b.append(ln)            # keep
            elif r < 0.8:
                b.append("MOD_" + ln)   # modify
            # else: delete (skip)
            if rng.random() < 0.15:
                b.append("NEW\n")       # insert
        # occasionally drop the final newline to exercise the no-eol path
        old = "".join(a)
        new = "".join(b)
        if b and rng.random() < 0.3:
            new = new[:-1] if new.endswith("\n") else new
        hunks = split_hunks(old, new)
        assert apply_hunks(old, hunks, set()) == old
        assert apply_hunks(old, hunks, _all(hunks)) == new


# --------------------------------------------------------------------------
# render_hunk
# --------------------------------------------------------------------------

def test_render_hunk_plain_and_colored() -> None:
    hunks = split_hunks("a\nb\nc\n", "a\nB\nc\n")
    plain = render_hunk(hunks[0], color=False)
    assert plain.splitlines()[0] == hunks[0].header  # header first
    assert "-b" in plain and "+B" in plain           # del then add
    assert "\033[" not in plain                      # no ANSI when color=False
    colored = render_hunk(hunks[0])
    assert "\033[" in colored                         # ANSI present by default


# --------------------------------------------------------------------------
# review SAFETY contract (the one impure fn): never silently drop edits.
# --------------------------------------------------------------------------

class _NullConsole:
    def print(self, *a, **k) -> None:  # noqa: D401 - swallow output
        pass


def test_review_no_hunks_returns_new() -> None:
    # Identical content → nothing to review → return new unchanged.
    assert review("same\n", "same\n", console=_NullConsole()) == "same\n"


def test_review_non_interactive_stdin_returns_new(monkeypatch) -> None:
    # Non-TTY stdin must apply the WHOLE change (today's behaviour), never drop it.
    class _FakeStdin:
        def isatty(self) -> bool:
            return False

    monkeypatch.setattr("sys.stdin", _FakeStdin())
    old, new = "a\nb\nc\n", "a\nB\nc\n"
    assert review(old, new, console=_NullConsole()) == new


def test_review_eof_returns_new(monkeypatch) -> None:
    # A genuine EOF (no stdin attribute / non-tty) must fall back to `new`.
    monkeypatch.setattr("sys.stdin", None)
    old, new = "x\ny\n", "x\nZ\n"
    assert review(old, new, console=_NullConsole()) == new
