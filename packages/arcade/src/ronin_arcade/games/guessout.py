"""Guess the Output — read a tiny snippet, predict exactly what it prints.

ronin's "what does this print?" arcade game. Each round shows a short
Python or JavaScript fragment full of classic gotchas — integer division,
list aliasing, late-binding closures, string immutability, truthiness, float
rounding, mutable default args, ``is`` vs ``==`` — and you type the *exact*
stdout. ronin reveals the real output and a one-line *why*, then keeps the
rounds coming until you quit. Score rewards correct answers and speed.

The puzzle bank (:data:`PUZZLES`) and the grader (:func:`check`) are pure and
I/O-free so they can be unit-tested without a terminal. ``check`` forgives only
surrounding/trailing whitespace — it stays value- and case-sensitive, because
``True`` is not ``true`` and ``3`` is not ``3.0``.
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
GOOD = "#9ece6a"   # correct
WARN = "#e0af68"   # what you wrote (when wrong)
ERROR = "#f7768e"  # incorrect
SKY = "#7dd3fc"    # the real output / lang tag
MUTE = "#6b7089"   # secondary text

# --------------------------------------------------------------------------- #
# The puzzle bank.  Each puzzle is a plain dict so it serialises trivially and
# stays easy to eyeball:
#   code   : str OR list[str]  — the snippet (no trailing newlines)
#   output : str               — the EXACT stdout it produces
#   why    : str               — one-line explanation of the gotcha
#   lang   : "python"|"javascript" (optional, for syntax highlighting)
# Every declared output has been verified against a real interpreter.
# --------------------------------------------------------------------------- #
PUZZLES: List[Dict[str, Any]] = [
    {
        # `code` as a bare string to exercise the str branch everywhere.
        "code": "print(7 // 2, 7 / 2)",
        "output": "3 3.5",
        "why": "// is floor division (stays int); / is true division (always float).",
        "lang": "python",
    },
    {
        "code": [
            "a = [1, 2, 3]",
            "b = a",
            "b.append(4)",
            "print(a)",
        ],
        "output": "[1, 2, 3, 4]",
        "why": "b = a binds the SAME list object, so appending through b also changes a.",
        "lang": "python",
    },
    {
        "code": [
            "funcs = [lambda: i for i in range(3)]",
            "print([f() for f in funcs])",
        ],
        "output": "[2, 2, 2]",
        "why": "The lambdas capture the variable i, not its value; after the loop i is 2.",
        "lang": "python",
    },
    {
        "code": [
            "s = 'hello'",
            "s.upper()",
            "print(s)",
        ],
        "output": "hello",
        "why": "Strings are immutable: .upper() returns a NEW string, so s is unchanged.",
        "lang": "python",
    },
    {
        "code": [
            "vals = [0, '', [], '0', None]",
            "print([bool(v) for v in vals])",
        ],
        "output": "[False, False, False, True, False]",
        "why": "0, '', [] and None are falsy; the non-empty string '0' is truthy.",
        "lang": "python",
    },
    {
        "code": "print(0.1 + 0.2, round(0.1 + 0.2, 1))",
        "output": "0.30000000000000004 0.3",
        "why": "Binary floats can't store 0.1 exactly, so the sum drifts; round() tidies it.",
        "lang": "python",
    },
    {
        "code": [
            "def tag(x, seen=[]):",
            "    seen.append(x)",
            "    return len(seen)",
            "print(tag('a'), tag('b'), tag('c'))",
        ],
        "output": "1 2 3",
        "why": "The default list is created ONCE and shared across calls, so it keeps growing.",
        "lang": "python",
    },
    {
        "code": [
            "a = [1, 2]",
            "b = [1, 2]",
            "print(a == b, a is b)",
        ],
        "output": "True False",
        "why": "== compares values (equal); is compares identity (two distinct objects).",
        "lang": "python",
    },
    {
        "code": "print(-7 % 3, 7 % -3)",
        "output": "2 -2",
        "why": "In Python the result of % takes the sign of the divisor, not the dividend.",
        "lang": "python",
    },
    {
        "code": [
            "grid = [[0] * 2] * 2",
            "grid[0][0] = 1",
            "print(grid)",
        ],
        "output": "[[1, 0], [1, 0]]",
        "why": "Outer * 2 copies the same inner-list reference, so editing one row edits both.",
        "lang": "python",
    },
    {
        "code": "print(True + True + False, sum([True, True, True]))",
        "output": "2 3",
        "why": "bool is a subclass of int: True counts as 1 and False as 0 in arithmetic.",
        "lang": "python",
    },
    {
        "code": [
            "s = 'racecar'",
            "print(s[::-1], s[1:4])",
        ],
        "output": "racecar ace",
        "why": "[::-1] reverses (a palindrome looks the same); [1:4] takes indices 1, 2, 3.",
        "lang": "python",
    },
    {
        "code": "print(int(3.99), int(-3.99))",
        "output": "3 -3",
        "why": "int() truncates toward zero — it chops the fraction, it does not round.",
        "lang": "python",
    },
    {
        "code": "print(0 or 'default', 'a' and 'b', 2 and 0)",
        "output": "default b 0",
        "why": "or returns the first truthy operand; and returns the first falsy one (else last).",
        "lang": "python",
    },
    {
        "code": "print(f'{2/3:.2f}', f'{1000000:,}')",
        "output": "0.67 1,000,000",
        "why": "':.2f' rounds to two decimals; ',' inserts thousands separators.",
        "lang": "python",
    },
    {
        "code": "console.log(1 + '2', '3' - 1)",
        "output": "12 2",
        "why": "+ with a string concatenates ('1'+'2'); - coerces the string to a number.",
        "lang": "javascript",
    },
    {
        "code": "console.log([10, 9, 100, 1].sort().join(','))",
        "output": "1,10,100,9",
        "why": "Array.sort() compares elements as STRINGS by default, so it sorts lexically.",
        "lang": "javascript",
    },
    {
        "code": "console.log(typeof NaN, NaN === NaN)",
        "output": "number false",
        "why": "typeof NaN is 'number', and NaN is the only value not equal to itself.",
        "lang": "javascript",
    },
]


# --------------------------------------------------------------------------- #
# Pure helpers + grader
# --------------------------------------------------------------------------- #
def _field(puzzle: Any, name: str, default: Any = None) -> Any:
    """Read ``name`` from a puzzle that may be a dict or a namedtuple/object."""
    if isinstance(puzzle, dict):
        return puzzle.get(name, default)
    return getattr(puzzle, name, default)


def _normalize(text: Any) -> str:
    """Forgive only surrounding/trailing whitespace; stay value-sensitive.

    Trailing whitespace on each line is dropped and blank leading/trailing
    lines are removed, but internal characters (and case) are preserved — so
    ``"3 3.5"`` and ``"  3 3.5  "`` match, while ``"3 3.6"`` does not.
    """
    s = str(text).replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.rstrip() for ln in s.split("\n")]
    # Final strip() forgives leading/trailing whitespace and blank edge lines
    # around the whole block while keeping interior characters value-sensitive.
    return "\n".join(lines).strip()


def check(puzzle: Any, guess: Any) -> bool:
    """Did ``guess`` predict the puzzle's exact stdout?

    Whitespace around the answer is forgiven; everything else is compared
    exactly. Any odd input (``None`` etc.) safely returns ``False``.
    """
    if guess is None:
        return False
    return _normalize(guess) == _normalize(_field(puzzle, "output", ""))


# Validate the bank at import time — fail loud if a puzzle is malformed.
assert PUZZLES, "PUZZLES must be non-empty"
for _p in PUZZLES:
    assert _field(_p, "code"), "puzzle missing non-empty 'code'"
    assert isinstance(_field(_p, "output"), str) and _field(_p, "output").strip(), \
        "puzzle missing non-empty 'output'"
    assert isinstance(_field(_p, "why"), str) and _field(_p, "why").strip(), \
        "puzzle missing non-empty 'why'"
del _p


def _score(elapsed: float, streak: int) -> int:
    """Points for a correct answer: 100 base + speed bonus + streak bonus."""
    speed = max(0, 50 - int(elapsed * 5))      # full under ~0s, gone by ~10s
    bonus = 5 * min(max(streak - 1, 0), 10)    # capped reward for momentum
    return 100 + speed + bonus


# --------------------------------------------------------------------------- #
# Rendering (terminal-only; kept defensive so a quirky terminal never crashes)
# --------------------------------------------------------------------------- #
def _lines_of(code: Any) -> List[str]:
    return code if isinstance(code, list) else str(code).splitlines()


def _render_snippet(console: Console, puzzle: Any) -> None:
    """Print the snippet with Rich syntax highlighting, falling back to plain."""
    from rich.text import Text

    lines = _lines_of(_field(puzzle, "code"))
    lang = _field(puzzle, "lang", "python") or "python"
    console.print(f"  [{SKY}]{lang}[/{SKY}]  [{MUTE}]· what does it print?[/{MUTE}]")
    console.print()
    try:
        from rich.syntax import Syntax

        src = "\n".join(lines)
        syntax = Syntax(
            src, lang, theme="dracula", line_numbers=False,
            word_wrap=False, background_color="default",
        )
        for line in syntax.highlight(src).split("\n"):
            if isinstance(line, Text):
                line.rstrip()
                console.print(Text("    ") + line, soft_wrap=True)
            else:  # pragma: no cover - defensive
                console.print(Text("    " + str(line)), soft_wrap=True)
    except Exception:  # pragma: no cover - highlighting is best-effort
        # Plain fallback via Text so brackets/braces in code aren't parsed as markup.
        for raw in lines:
            console.print(Text("    " + raw, style="white"), soft_wrap=True)
    console.print()


def _emit(console: Console, prefix: str, value: Any, style: str) -> None:
    """Print ``prefix`` then ``value`` as literal text (no markup parsing).

    Safe for outputs/explanations containing ``[``, ``]``, ``{`` or ``//``.
    """
    from rich.text import Text

    out_lines = str(value).split("\n")
    if len(out_lines) <= 1:
        console.print(Text(f"  {prefix} ", style=MUTE) + Text(out_lines[0], style=style))
    else:
        console.print(Text(f"  {prefix}", style=MUTE))
        for ol in out_lines:
            console.print(Text("      " + ol, style=style))


def _play(console: Console) -> None:
    header(console, GAME)
    console.print(
        f"  [{MUTE}]Read each snippet and type its EXACT output. Surrounding "
        f"whitespace is forgiven; case and values matter. Faster correct "
        f"answers score more. (q or empty line to quit)[/{MUTE}]\n"
    )

    deck: List[Any] = []
    score = streak = best_streak = asked = correct_count = 0

    while True:
        if not deck:                       # endless rounds: reshuffle the bank
            deck = list(PUZZLES)
            random.shuffle(deck)
        puzzle = deck.pop()

        console.print(f"  [{MUTE}]── round {asked + 1} ──[/{MUTE}]")
        _render_snippet(console, puzzle)

        start = time.monotonic()
        raw = ask_line(console, "predict the output:")
        elapsed = time.monotonic() - start

        if raw.strip().lower() in ("", "q", "quit"):
            break

        asked += 1
        if check(puzzle, raw):
            streak += 1
            best_streak = max(best_streak, streak)
            gained = _score(elapsed, streak)
            score += gained
            correct_count += 1
            console.print(
                f"\n  [bold {GOOD}]✓ correct[/bold {GOOD}]  "
                f"[{MUTE}]+{gained} pts · {elapsed:.1f}s · streak {streak}[/{MUTE}]"
            )
        else:
            streak = 0
            console.print(f"\n  [bold {ERROR}]✗ not quite[/bold {ERROR}]")
            _emit(console, "you wrote:", raw, WARN)

        _emit(console, "it prints:", _field(puzzle, "output"), SKY)
        _emit(console, "why:      ", _field(puzzle, "why"), MUTE)
        console.print(f"  [{TEAL}]score {score}[/{TEAL}]\n")

    if asked:
        console.print(
            f"\n  [bold {TEAL}]final {score} pts[/bold {TEAL}]  "
            f"[{MUTE}]{correct_count}/{asked} correct · best streak {best_streak}[/{MUTE}]"
        )
    else:
        console.print(f"  [{MUTE}]No rounds played — come back any time.[/{MUTE}]")


GAME = GameMeta(
    key="guessout",
    name="Guess the Output",
    emoji="🖨️",
    desc="predict what the code prints",
    play=_play,
)
