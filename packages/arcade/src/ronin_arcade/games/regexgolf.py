"""Regex Golf — write the shortest regex that threads the needle.

Each level hands you a MATCH list (strings your pattern must hit) and an AVOID
list (strings it must miss). Type a Python regex; ronin runs ``re.search`` on
every string and scores you by *correctness first, brevity second* — like golf,
the shorter the winning pattern the better. Every level ships with a ``par``,
the length of a tidy reference solution to beat.

The rules live in pure, I/O-free functions (``solves``, plus the ``LEVELS``
bank) so they can be unit-tested without a terminal; ``_play`` wires them to a
Rich console. An invalid regex never raises here — ``solves`` simply returns
False, and the play loop reports the error and lets you try again.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from rich.console import Console

from ._engine import GameMeta, ask_line, header

# --- palette --------------------------------------------------------------
TEAL = "#2dd4bf"
GOOD = "#9ece6a"
ERROR = "#f7768e"
WARN = "#e0af68"
SKY = "#7dd3fc"
MUTE = "#6b7089"


# --- level model ----------------------------------------------------------
@dataclass(frozen=True)
class Level:
    """One regex-golf puzzle.

    ``match`` strings must all be matched; ``avoid`` strings must all be missed.
    ``name`` is a short label, ``hint`` a nudge, ``par`` the length of a known
    tidy solution worth beating.
    """
    name: str
    match: list[str]
    avoid: list[str]
    par: int
    hint: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)


# --- pure rules -----------------------------------------------------------
def solves(pattern: str, match_list, avoid_list) -> bool:
    """True iff ``re.search(pattern, s)`` is truthy for every ``s`` in
    ``match_list`` and falsy for every ``s`` in ``avoid_list``.

    An empty pattern, or any pattern that fails to compile, returns False — it
    never raises. (An empty pattern would match everything, so it can't avoid
    anything; we reject it up front to keep "" from being a cheap win.)
    """
    if not isinstance(pattern, str) or pattern == "":
        return False
    try:
        rx = re.compile(pattern)
    except re.error:
        return False
    try:
        for s in match_list:
            if rx.search(s) is None:
                return False
        for s in avoid_list:
            if rx.search(s) is not None:
                return False
    except (TypeError, re.error):
        return False
    return True


# --- level bank -----------------------------------------------------------
# ~10 levels, easy → hard. Each is solvable (a reference solver length == par
# is checked by the self-test at import-only time via VERIFY, not at runtime).
LEVELS: list[Level] = [
    Level(
        name="plain cats",
        match=["cat", "scatter", "category", "tomcat"],
        avoid=["dog", "bird", "fish", "mouse"],
        par=3,
        hint="the substring they all share is staring at you",
        tags=("substring",),
    ),
    Level(
        name="ends in -ing",
        match=["running", "coding", "sailing", "thing"],
        avoid=["singer", "ink", "ringtone", "kingdom"],
        par=4,
        hint="anchor to the end with $",
        tags=("anchor",),
    ),
    Level(
        name="starts with a vowel",
        match=["apple", "otter", "image", "ember", "ultra"],
        avoid=["banana", "tree", "ronin", "glyph"],
        par=8,
        hint="a character class anchored at the start: ^[...]",
        tags=("class", "anchor"),
    ),
    Level(
        name="has a digit",
        match=["a1", "v2.0", "room42", "h2o"],
        avoid=["alpha", "ronin", "teal", "vector"],
        par=2,
        hint="\\d is your friend",
        tags=("digit",),
    ),
    Level(
        name="double letter",
        match=["balloon", "coffee", "stress", "puddle"],
        avoid=["ronin", "teal", "vapor", "binary"],
        par=11,
        hint="a backreference: (.)\\1 catches any repeated char",
        tags=("backref",),
    ),
    Level(
        name="three-letter words",
        match=["cat", "dog", "ronin"[:3], "sky"],
        avoid=["tiger", "io", "abcd", "house"],
        par=8,
        hint="anchor both ends and count: ^...$ or ^.{3}$",
        tags=("anchor", "length"),
    ),
    Level(
        name="hex color",
        match=["#fff", "#1a2b3c", "#0F0", "#abcdef"],
        avoid=["fff", "#ghi", "color", "#zz"],
        par=13,
        hint="a # then hex digits: #[0-9a-fA-F]+ (or a shorter class)",
        tags=("class", "anchor"),
    ),
    Level(
        name="dot-com domains",
        match=["a.com", "ronin.com", "x.com", "shop.com"],
        avoid=["a.org", "com", "comet", "a.com.net"],
        par=6,
        hint="match the literal end '.com' — remember . is special, anchor it",
        tags=("escape", "anchor"),
    ),
    Level(
        name="no e allowed",
        match=["ronin", " on", "town", "knish"],
        avoid=["teal", "ember", "code", "green"],
        par=8,
        hint="match a string that has no 'e' anywhere: ^[^e]*$ / ^[^e]+$",
        tags=("negate", "anchor"),
    ),
    Level(
        name="snake_case ids",
        match=["foo_bar", "x_y", "ronin_kit", "a_b_c"],
        avoid=["fooBar", "foo-bar", "foobar", "_lead"],
        par=11,
        hint="word, underscore, word: \\w+_\\w+ but keep the leading char real",
        tags=("class",),
    ),
    Level(
        name="balanced parens word",
        match=["(a)", "(hi)", "(ronin)", "(x)"],
        avoid=["a)", "(a", "()", "[a]"],
        par=8,
        hint="literal parens around at least one char: \\(.+\\)",
        tags=("escape",),
    ),
]


# --- scoring --------------------------------------------------------------
def _per_string_report(rx, strings, *, should_match: bool):
    """Yield ``(string, ok)`` tuples — ok means the string is on the right side
    of the line for this column (matched when it should, or missed when it
    should)."""
    rows = []
    for s in strings:
        try:
            hit = rx.search(s) is not None
        except (TypeError, re.error):
            hit = False
        ok = hit if should_match else (not hit)
        rows.append((s, ok))
    return rows


def _render_level(console: Console, level: Level, idx: int, total: int) -> None:
    console.print(
        f"  [{SKY}]level {idx + 1}/{total}[/{SKY}]   "
        f"[bold {TEAL}]{level.name}[/bold {TEAL}]   "
        f"[{MUTE}]par {level.par}[/{MUTE}]"
    )
    console.print()
    console.print(f"  [{GOOD}]MATCH[/{GOOD}]  [{MUTE}](your regex must hit all of these)[/{MUTE}]")
    console.print("    " + "  ".join(f"[{GOOD}]{s}[/{GOOD}]" for s in level.match))
    console.print()
    console.print(f"  [{ERROR}]AVOID[/{ERROR}]  [{MUTE}](and miss every one of these)[/{MUTE}]")
    console.print("    " + "  ".join(f"[{ERROR}]{s}[/{ERROR}]" for s in level.avoid))
    console.print()


def _show_result(console: Console, level: Level, pattern: str) -> bool:
    """Print a per-string pass/fail grid for ``pattern`` and return whether it
    fully solves the level. Tolerant of invalid patterns."""
    try:
        rx = re.compile(pattern)
    except re.error as exc:
        console.print(f"  [{ERROR}]✗ invalid regex:[/{ERROR}] [{MUTE}]{exc}[/{MUTE}]\n")
        return False

    match_rows = _per_string_report(rx, level.match, should_match=True)
    avoid_rows = _per_string_report(rx, level.avoid, should_match=False)

    console.print(f"  [{MUTE}]pattern[/{MUTE}] [{TEAL}]/{pattern}/[/{TEAL}]  "
                  f"[{MUTE}]({len(pattern)} chars)[/{MUTE}]")
    console.print()
    for s, ok in match_rows:
        mark = f"[{GOOD}]✓[/{GOOD}]" if ok else f"[{ERROR}]✗[/{ERROR}]"
        verb = "matched" if ok else "MISSED (should match)"
        color = GOOD if ok else ERROR
        console.print(f"    {mark} [{color}]{s}[/{color}]  [{MUTE}]{verb}[/{MUTE}]")
    for s, ok in avoid_rows:
        mark = f"[{GOOD}]✓[/{GOOD}]" if ok else f"[{ERROR}]✗[/{ERROR}]"
        verb = "avoided" if ok else "MATCHED (should avoid)"
        color = GOOD if ok else ERROR
        console.print(f"    {mark} [{color}]{s}[/{color}]  [{MUTE}]{verb}[/{MUTE}]")
    console.print()

    return solves(pattern, level.match, level.avoid)


def _grade(pattern_len: int, par: int) -> str:
    """A golf-style grade line comparing pattern length to par."""
    diff = pattern_len - par
    if diff <= -2:
        return f"[bold {TEAL}]eagle! {-diff} under par[/bold {TEAL}]"
    if diff == -1:
        return f"[{GOOD}]birdie — 1 under par[/{GOOD}]"
    if diff == 0:
        return f"[{GOOD}]par[/{GOOD}]"
    if diff == 1:
        return f"[{WARN}]bogey — 1 over par[/{WARN}]"
    return f"[{WARN}]{diff} over par[/{WARN}]"


def _play(console: Console) -> None:
    header(console, GAME)
    console.print(
        f"  [{MUTE}]Type a Python regex that MATCHES every green string and "
        f"AVOIDS every red one.[/{MUTE}]"
    )
    console.print(
        f"  [{MUTE}]Shorter is better — beat par. Type [/{MUTE}]"
        f"[{SKY}]skip[/{SKY}][{MUTE}] to pass, [/{MUTE}]"
        f"[{SKY}]q[/{SKY}][{MUTE}] to quit.[/{MUTE}]"
    )
    console.print()

    total = len(LEVELS)
    solved = 0
    total_over_par = 0
    best_lengths: list[int] = []

    for idx, level in enumerate(LEVELS):
        console.print(f"  [{MUTE}]{'─' * 48}[/{MUTE}]")
        console.print()
        _render_level(console, level, idx, total)
        if level.hint:
            console.print(f"  [{WARN}]hint[/{WARN}]  [{MUTE}]{level.hint}[/{MUTE}]")
            console.print()

        won = False
        attempts = 0
        while True:
            raw = ask_line(console, "regex:")
            if raw in ("q", "quit"):
                console.print(f"\n  [{MUTE}]Walking off the course. Later![/{MUTE}]\n")
                _final(console, solved, total, best_lengths)
                return
            if raw in ("skip", "s", ""):
                console.print(f"  [{MUTE}]skipped — the green stays green elsewhere.[/{MUTE}]\n")
                break

            attempts += 1
            ok = _show_result(console, level, raw)
            if ok:
                won = True
                solved += 1
                best_lengths.append(len(raw))
                over = max(0, len(raw) - level.par)
                total_over_par += over
                console.print(
                    f"  [bold {GOOD}]✓ cleared![/bold {GOOD}]  "
                    f"{_grade(len(raw), level.par)}"
                )
                if attempts == 1 and len(raw) <= level.par:
                    console.print(f"  [{TEAL}]hole in one ✦[/{TEAL}]")
                console.print()
                break
            else:
                console.print(
                    f"  [{ERROR}]not yet — fix the ✗ rows above and try again "
                    f"(or 'skip').[/{ERROR}]\n"
                )

        if not won and raw not in ("skip", "s", ""):
            # Only reachable via 'q', already handled. Defensive no-op.
            pass

    _final(console, solved, total, best_lengths)


def _final(console: Console, solved: int, total: int, best_lengths: list[int]) -> None:
    console.print(f"  [{MUTE}]{'─' * 48}[/{MUTE}]")
    chars = sum(best_lengths)
    if solved == total:
        console.print(
            f"\n  [bold {TEAL}]⛳ Full course cleared — {solved}/{total} holes![/bold {TEAL}]"
        )
    else:
        console.print(
            f"\n  [bold {GOOD}]You cleared {solved}/{total} holes.[/bold {GOOD}]"
        )
    if best_lengths:
        console.print(
            f"  [{SKY}]total winning regex length: {chars} chars[/{SKY}]  "
            f"[{MUTE}](lower is better)[/{MUTE}]"
        )
    console.print()


GAME = GameMeta(
    key="regex",
    name="Regex Golf",
    emoji="🧩",
    desc="shortest regex that matches the targets",
    play=_play,
)
