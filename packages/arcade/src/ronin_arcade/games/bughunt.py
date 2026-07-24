"""Bug Hunt — spot the planted bug in a short function, ronin's debugging arcade.

A real little code-review game: each round shows an 8-15 line Python or
JavaScript function with exactly ONE deliberately planted bug. The player reads
the code (rendered with Rich syntax highlighting, gutter line numbers in mute)
and guesses the 1-based line number that holds the bug. After each guess the bug
line is revealed in rose, with a plain-English explanation and the one-line fix
in green. Score rewards a correct line *and* speed — answer fast for bonus
points. Keep hunting until you quit.

The puzzle bank and the ``check`` rule are pure and I/O-free so they can be
unit-tested without a terminal.
"""
from __future__ import annotations

import random
import time
from typing import Any, Dict, List

from rich.console import Console

from ._engine import GameMeta, ask_line, header

# --------------------------------------------------------------------------- #
# Palette (ronin soft pastels)
# --------------------------------------------------------------------------- #
TEAL = "#2dd4bf"   # accent
GOOD = "#9ece6a"   # correct / the fix
WARN = "#e0af68"   # close / hints
ERR = "#f7768e"    # the buggy line on reveal
SKY = "#7dd3fc"    # lang tag / info
MUTE = "#6b7089"   # gutter line numbers, secondary text

# --------------------------------------------------------------------------- #
# Puzzle bank — each puzzle is a dict:
#   lang        : "python" | "javascript"  (for syntax highlighting)
#   code        : list[str] of source lines (no trailing newlines)
#   bug_line    : 1-based line number of the planted bug
#   explanation : why that line is wrong
#   fix         : the corrected line / one-line fix
# Every bug_line is validated against its code length at import time.
# --------------------------------------------------------------------------- #
PUZZLES: List[Dict[str, Any]] = [
    {
        "lang": "python",
        "code": [
            "def average(numbers):",
            "    total = 0",
            "    for n in numbers:",
            "        total += n",
            "    return total / len(numbers) + 1",
        ],
        "bug_line": 5,
        "explanation": "The `+ 1` is bogus — it inflates every average by one. "
                       "Operator precedence means it's added after the division.",
        "fix": "    return total / len(numbers)",
    },
    {
        "lang": "python",
        "code": [
            "def first_even(items):",
            "    for i in range(len(items)):",
            "        if items[i] % 2 == 1:",
            "            return items[i]",
            "    return None",
        ],
        "bug_line": 3,
        "explanation": "`% 2 == 1` tests for ODD numbers, so this returns the "
                       "first odd value instead of the first even one.",
        "fix": "        if items[i] % 2 == 0:",
    },
    {
        "lang": "python",
        "code": [
            "def count_vowels(word):",
            "    vowels = 'aeiou'",
            "    count = 0",
            "    for ch in word:",
            "        if ch in vowels:",
            "            count = 1",
            "    return count",
        ],
        "bug_line": 6,
        "explanation": "`count = 1` overwrites the running total on every vowel, "
                       "so the result is always 0 or 1. It should accumulate.",
        "fix": "            count += 1",
    },
    {
        "lang": "python",
        "code": [
            "def make_counters(n):",
            "    counters = []",
            "    for i in range(n):",
            "        counters.append(lambda: i)",
            "    return counters",
        ],
        "bug_line": 4,
        "explanation": "Classic late-binding closure bug: every lambda captures "
                       "`i` by reference, so they all return n-1 after the loop.",
        "fix": "        counters.append(lambda i=i: i)",
    },
    {
        "lang": "python",
        "code": [
            "def add_item(item, bucket=[]):",
            "    bucket.append(item)",
            "    return bucket",
            "",
            "first = add_item('a')",
            "second = add_item('b')",
        ],
        "bug_line": 1,
        "explanation": "Mutable default argument: the same list is reused across "
                       "calls, so `second` is ['a', 'b'], not ['b'].",
        "fix": "def add_item(item, bucket=None):  # then: bucket = bucket or []",
    },
    {
        "lang": "python",
        "code": [
            "def factorial(n):",
            "    result = 1",
            "    for i in range(1, n):",
            "        result *= i",
            "    return result",
        ],
        "bug_line": 3,
        "explanation": "`range(1, n)` stops at n-1, so the final factor n is never "
                       "multiplied in. factorial(5) gives 24 instead of 120.",
        "fix": "    for i in range(1, n + 1):",
    },
    {
        "lang": "python",
        "code": [
            "def is_palindrome(s):",
            "    s = s.lower()",
            "    left, right = 0, len(s) - 1",
            "    while left < right:",
            "        if s[left] != s[right]:",
            "            return False",
            "        left += 1",
            "        right += 1",
            "    return True",
        ],
        "bug_line": 8,
        "explanation": "`right` should move inward but it's incremented, so it runs "
                       "off the end and the loop never converges (IndexError).",
        "fix": "        right -= 1",
    },
    {
        "lang": "python",
        "code": [
            "def find_max(values):",
            "    best = 0",
            "    for v in values:",
            "        if v > best:",
            "            best = v",
            "    return best",
        ],
        "bug_line": 2,
        "explanation": "Seeding `best = 0` breaks all-negative inputs: find_max("
                       "[-3, -1, -7]) returns 0, which isn't even in the list.",
        "fix": "    best = values[0]",
    },
    {
        "lang": "python",
        "code": [
            "def dedupe(items):",
            "    seen = set()",
            "    out = []",
            "    for x in items:",
            "        if x in seen:",
            "            out.append(x)",
            "        seen.add(x)",
            "    return out",
        ],
        "bug_line": 5,
        "explanation": "The condition is inverted: it appends items it has ALREADY "
                       "seen, so `out` collects the duplicates, not the uniques.",
        "fix": "        if x not in seen:",
    },
    {
        "lang": "javascript",
        "code": [
            "function sum(arr) {",
            "  let total = 0;",
            "  for (let i = 0; i <= arr.length; i++) {",
            "    total += arr[i];",
            "  }",
            "  return total;",
            "}",
        ],
        "bug_line": 3,
        "explanation": "Off-by-one: `i <= arr.length` reads one past the end, so "
                       "arr[i] is undefined on the last pass and total becomes NaN.",
        "fix": "  for (let i = 0; i < arr.length; i++) {",
    },
    {
        "lang": "javascript",
        "code": [
            "function isAdult(age) {",
            "  if (age = 18) {",
            "    return true;",
            "  }",
            "  return false;",
            "}",
        ],
        "bug_line": 2,
        "explanation": "Assignment `=` instead of comparison. It sets age to 18 and "
                       "evaluates truthy, so every call returns true.",
        "fix": "  if (age >= 18) {",
    },
    {
        "lang": "javascript",
        "code": [
            "function greet(names) {",
            "  let msg = '';",
            "  for (let i = 0; i < names.length; i++) {",
            "    msg += 'Hi ' + names[i] + ', ';",
            "  }",
            "  return msg.slice(0, 2);",
            "}",
        ],
        "bug_line": 6,
        "explanation": "`slice(0, 2)` keeps only the first two characters of the "
                       "whole message. It was meant to drop the trailing ', '.",
        "fix": "  return msg.slice(0, -2);",
    },
    {
        "lang": "javascript",
        "code": [
            "function contains(arr, target) {",
            "  for (let i = 0; i < arr.length; i++) {",
            "    if (arr[i] === target) {",
            "      return false;",
            "    }",
            "  }",
            "  return true;",
            "}",
        ],
        "bug_line": 4,
        "explanation": "The return values are swapped: a match returns false and "
                       "no match returns true — the function reports the opposite.",
        "fix": "      return true;",
    },
    {
        "lang": "python",
        "code": [
            "def safe_div(a, b):",
            "    try:",
            "        return a / b",
            "    except ValueError:",
            "        return 0",
        ],
        "bug_line": 4,
        "explanation": "Dividing by zero raises ZeroDivisionError, not ValueError, "
                       "so this handler never fires and the error escapes.",
        "fix": "    except ZeroDivisionError:",
    },
    {
        "lang": "python",
        "code": [
            "def merge(a, b):",
            "    result = a",
            "    for key in b:",
            "        result[key] = b[key]",
            "    return result",
        ],
        "bug_line": 2,
        "explanation": "`result = a` aliases the input dict, so merge() mutates the "
                       "caller's `a` in place — a surprising side effect.",
        "fix": "    result = dict(a)",
    },
    {
        "lang": "python",
        "code": [
            "def chunk(items, size):",
            "    out = []",
            "    for i in range(0, len(items), size):",
            "        out.append(items[i:i + size])",
            "    return out[0]",
        ],
        "bug_line": 5,
        "explanation": "`out[0]` returns only the first chunk. The whole list of "
                       "chunks should be returned.",
        "fix": "    return out",
    },
]

# --------------------------------------------------------------------------- #
# Validate the bank at import time — every bug_line must be a real line.
# --------------------------------------------------------------------------- #
for _idx, _p in enumerate(PUZZLES):
    _n = len(_p["code"])
    if not (1 <= _p["bug_line"] <= _n):
        raise ValueError(
            f"PUZZLES[{_idx}]: bug_line {_p['bug_line']} out of range 1..{_n}"
        )


# --------------------------------------------------------------------------- #
# Pure rule (unit-testable)
# --------------------------------------------------------------------------- #
def check(puzzle: Dict[str, Any], guess: int) -> bool:
    """Return True iff the guessed 1-based line is the puzzle's bug line."""
    try:
        return int(guess) == int(puzzle["bug_line"])
    except (TypeError, ValueError):
        return False


def score_for(elapsed: float, *, base: int = 100) -> int:
    """Points for a correct guess: full marks for a fast answer, decaying with
    time but never below a small floor. Pure helper, safe to test."""
    try:
        penalty = int(max(0.0, elapsed) * 4)
    except (TypeError, ValueError):
        penalty = 0
    return max(20, base - penalty)


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def _render_code(console: Console, puzzle: Dict[str, Any]) -> None:
    """Print the snippet with 1-based gutter line numbers in mute.

    Tries Rich ``Syntax`` for real highlighting; falls back to plain numbered
    lines if anything goes sideways (never crashes the game).
    """
    code = puzzle["code"]
    lang = puzzle.get("lang", "python")
    width = len(str(len(code)))

    console.print(f"  [{SKY}]{lang}[/{SKY}]  [{MUTE}]· {len(code)} lines[/{MUTE}]")
    console.print()

    try:
        from rich.syntax import Syntax
        from rich.text import Text

        src = "\n".join(code)
        syntax = Syntax(
            src,
            lang,
            theme="dracula",
            line_numbers=False,
            word_wrap=False,
            background_color="default",
        )
        # Render the highlighted source to per-line Text, then prepend our own
        # mute gutter so the numbering matches the 1-based bug_line exactly.
        lines = syntax.highlight(src).split("\n")
        for i, line in enumerate(lines, start=1):
            gutter = Text(f"  {str(i).rjust(width)} │ ", style=MUTE)
            if isinstance(line, Text):
                line.rstrip()
            console.print(gutter + line, soft_wrap=True)
    except Exception:
        for i, raw in enumerate(code, start=1):
            console.print(
                f"  [{MUTE}]{str(i).rjust(width)} │[/{MUTE}] {raw}",
                soft_wrap=True,
            )
    console.print()


def _reveal(console: Console, puzzle: Dict[str, Any]) -> None:
    """Show the buggy line (rose), the explanation, and the fix (green)."""
    n = puzzle["bug_line"]
    buggy = puzzle["code"][n - 1]
    console.print(
        f"  [bold {ERR}]✗ line {n}[/bold {ERR}]  "
        f"[{ERR}]{buggy.strip()}[/{ERR}]"
    )
    console.print(f"    [{MUTE}]{puzzle['explanation']}[/{MUTE}]")
    console.print(
        f"  [bold {GOOD}]✓ fix[/bold {GOOD}]   "
        f"[{GOOD}]{puzzle['fix'].strip()}[/{GOOD}]"
    )
    console.print()


# --------------------------------------------------------------------------- #
# Interactive loop
# --------------------------------------------------------------------------- #
def _play(console: Console) -> None:
    header(console, GAME)
    console.print(
        f"  [{MUTE}]Each snippet has exactly one bug. Type the "
        f"[bold {TEAL}]line number[/bold {TEAL}] you think is wrong.\n"
        f"  Answer fast for bonus points. Type "
        f"[bold {TEAL}]q[/bold {TEAL}] anytime to quit.[/]"
    )
    console.print()

    order = list(range(len(PUZZLES)))
    random.shuffle(order)

    score = 0
    solved = 0
    rounds = 0

    for pi in order:
        puzzle = PUZZLES[pi]
        rounds += 1
        n = len(puzzle["code"])

        console.print(
            f"  [bold {TEAL}]Round {rounds}[/bold {TEAL}]  "
            f"[{MUTE}]· score {score}[/{MUTE}]"
        )
        _render_code(console, puzzle)

        started = time.monotonic()
        raw = ask_line(console, f"buggy line (1-{n}):")
        elapsed = time.monotonic() - started
        low = raw.strip().lower()

        if low in ("q", "quit", ""):
            console.print(f"\n  [{MUTE}]Bug line was "
                          f"[bold {TEAL}]{puzzle['bug_line']}[/bold {TEAL}].[/]")
            _reveal(console, puzzle)
            break

        try:
            guess = int(low)
        except ValueError:
            console.print(f"  [{WARN}]Enter a line number between 1 and {n}.[/]")
            _reveal(console, puzzle)
            console.print(f"  [{MUTE}]──────────[/{MUTE}]\n")
            continue

        if guess < 1 or guess > n:
            console.print(
                f"  [{WARN}]Line {guess} is out of range (1-{n}) — counts as a miss.[/]"
            )
            _reveal(console, puzzle)
            console.print(f"  [{MUTE}]──────────[/{MUTE}]\n")
            continue

        if check(puzzle, guess):
            pts = score_for(elapsed)
            score += pts
            solved += 1
            console.print(
                f"  [bold {GOOD}]Nailed it![/bold {GOOD}] "
                f"[{GOOD}]+{pts}[/{GOOD}] "
                f"[{MUTE}]({elapsed:.1f}s)[/{MUTE}]"
            )
        else:
            console.print(
                f"  [{ERR}]Not quite.[/{ERR}] "
                f"[{MUTE}]The bug was on line {puzzle['bug_line']}, not {guess}.[/]"
            )
        console.print()
        _reveal(console, puzzle)
        console.print(f"  [{MUTE}]──────────[/{MUTE}]\n")

    if rounds:
        console.print(
            f"  [bold {TEAL}]Bug hunt over.[/bold {TEAL}] "
            f"[{MUTE}]Solved[/{MUTE}] [bold {GOOD}]{solved}[/bold {GOOD}] "
            f"[{MUTE}]·[/{MUTE}] [bold {TEAL}]{score}[/bold {TEAL}] "
            f"[{MUTE}]points.[/]\n"
        )


GAME = GameMeta(key="bughunt", name="Bug Hunt", emoji="🐛",
                desc="spot the bug in the code", play=_play)
