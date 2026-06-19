"""Typing Test — how fast can you type code?

A real typing-speed test for developers: a short code line or programming quote
is shown in a panel, the player types it back, and the round is timed with the
wall clock. Each round reports words-per-minute, accuracy, and elapsed time,
and a personal best (by WPM) is tracked across rounds until the player quits.

The scoring *rules* live in pure, I/O-free functions (``wpm`` and ``accuracy``)
so they can be unit-tested without a terminal; ``_play`` wires them to a Rich
console.
"""
from __future__ import annotations

import random
import time

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from ._engine import GameMeta, ask_line, header

# --- palette -----------------------------------------------------------------
TEAL = "#2dd4bf"
GOOD = "#9ece6a"
WARN = "#e0af68"
ERROR = "#f7768e"
SKY = "#7dd3fc"
MUTE = "#6b7089"

# --- snippets: ~20 short code lines & developer quotes -----------------------
SNIPPETS: list[str] = [
    "for i in range(len(items)):",
    "def main() -> None:",
    "print(f'hello, {name}!')",
    "result = [x * 2 for x in nums if x > 0]",
    "git commit -m 'fix off-by-one error'",
    "const sum = (a, b) => a + b;",
    "if (err) throw new Error('boom');",
    "SELECT id, name FROM users WHERE active = 1;",
    "return self.value or default",
    "import numpy as np",
    "try { run(); } catch (e) { log(e); }",
    "let mut total: u64 = 0;",
    "export default function App() {}",
    "raise ValueError('unexpected token')",
    "docker run -it --rm ubuntu:latest bash",
    "There are only two hard things in computer science.",
    "Premature optimization is the root of all evil.",
    "Talk is cheap. Show me the code.",
    "Code is read more often than it is written.",
    "Make it work, make it right, make it fast.",
    "Any fool can write code a computer understands.",
    "Simplicity is the soul of efficiency.",
    "First, solve the problem. Then, write the code.",
    "while True:\n    break",
    "assert x == 42, 'sanity check failed'",
]


# --- pure rules (I/O-free, unit-testable) ------------------------------------
def wpm(typed_chars: int, seconds: float) -> float:
    """Words per minute using the standard 5-chars-per-word convention.

    ``(chars / 5) / (seconds / 60)``. Returns 0.0 for non-positive time or a
    non-positive character count, so callers never divide by zero.
    """
    if seconds <= 0 or typed_chars <= 0:
        return 0.0
    return (typed_chars / 5.0) / (seconds / 60.0)


def accuracy(target: str, typed: str) -> float:
    """Percentage (0-100) of character positions in ``target`` typed correctly.

    Compared position-by-position against ``target``; length differences are
    handled by scoring missing/extra positions as mismatches. An empty target
    yields 100.0 only when nothing was typed, else 0.0.
    """
    n = len(target)
    if n == 0:
        return 100.0 if len(typed) == 0 else 0.0
    matches = sum(1 for i in range(n) if i < len(typed) and typed[i] == target[i])
    return matches / n * 100.0


# --- presentation helpers ----------------------------------------------------
def _acc_color(acc: float) -> str:
    if acc >= 95.0:
        return GOOD
    if acc >= 80.0:
        return WARN
    return ERROR


def _diff_text(target: str, typed: str) -> Text:
    """A per-character colored rendering: correct=good, wrong=error, missing=mute."""
    out = Text()
    for i, ch in enumerate(target):
        display = "↵\n" if ch == "\n" else ch
        if i < len(typed) and typed[i] == ch:
            out.append(display, style=GOOD)
        elif i < len(typed):
            # wrong char: show what they typed where it differs (or a marker)
            shown = typed[i] if typed[i] != "\n" else "↵"
            out.append(shown if ch != "\n" else display, style=f"bold {ERROR}")
        else:
            out.append(display, style=MUTE)
    return out


def _panel(snippet: str) -> Panel:
    body = Text(snippet, style="bold")
    return Panel(
        body,
        title=f"[{TEAL}]type this[/{TEAL}]",
        border_style=SKY,
        padding=(1, 2),
    )


# --- interactive entry point -------------------------------------------------
def _play(console: Console) -> None:
    header(console, GAME)
    console.print(f"  [{MUTE}]Type the snippet exactly, then press Enter. "
                  f"Type [/{MUTE}][{TEAL}]q[/{TEAL}][{MUTE}] at the prompt to quit.[/{MUTE}]")

    best_wpm = 0.0
    best_acc = 0.0
    rounds = 0
    deck: list[str] = []

    while True:
        if not deck:
            deck = SNIPPETS[:]
            random.shuffle(deck)
        snippet = deck.pop()

        console.print()
        console.print(_panel(snippet))

        start = time.monotonic()
        typed = ask_line(console, "")
        end = time.monotonic()

        if typed.strip().lower() == "q":
            break

        elapsed = max(end - start, 0.0)
        acc = accuracy(snippet, typed)
        speed = wpm(len(typed), elapsed)
        rounds += 1

        acc_c = _acc_color(acc)
        console.print()
        # render the colored diff with markup-safe Text
        diff = _diff_text(snippet, typed)
        diff.pad_left(2)
        console.print(diff)
        console.print()

        is_best_wpm = speed > best_wpm
        if is_best_wpm:
            best_wpm = speed
        if acc > best_acc:
            best_acc = acc

        summary = Text("  ")
        summary.append("WPM ", style=MUTE)
        summary.append(f"{speed:5.1f}", style=f"bold {TEAL}")
        summary.append("   acc ", style=MUTE)
        summary.append(f"{acc:5.1f}%", style=f"bold {acc_c}")
        summary.append("   time ", style=MUTE)
        summary.append(f"{elapsed:4.1f}s", style=f"bold {SKY}")
        if is_best_wpm and rounds > 1:
            summary.append("   ★ new best!", style=f"bold {GOOD}")
        console.print(summary)

    # --- final tally ---------------------------------------------------------
    console.print()
    if rounds == 0:
        console.print(f"  [{MUTE}]No rounds played — come back faster next time.[/{MUTE}]")
        return

    final = Text("  ")
    final.append(f"{rounds} round{'s' if rounds != 1 else ''}", style=f"bold {SKY}")
    final.append("   personal best ", style=MUTE)
    final.append(f"{best_wpm:.1f} WPM", style=f"bold {TEAL}")
    final.append("   peak acc ", style=MUTE)
    final.append(f"{best_acc:.1f}%", style=f"bold {_acc_color(best_acc)}")
    console.print(final)
    console.print()


GAME = GameMeta(
    key="typing",
    name="Typing Test",
    emoji="⌨️",
    desc="how fast can you type code?",
    play=_play,
)
