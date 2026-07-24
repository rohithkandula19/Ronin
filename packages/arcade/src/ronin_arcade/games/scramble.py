"""Word Scramble — unscramble the jumbled word, against a soft timer.

A themed word is shuffled and shown as spaced-out tiles. The player guesses
the original word; a per-word hint (definition / category) can be revealed, and
letter-by-letter hints peel the puzzle open one tile at a time. Scoring rewards
speed (a soft per-round timer) and consecutive solves (a streak bonus). The
rules live in pure functions (``scramble``, ``is_correct``) so they can be
unit-tested without a terminal; ``_play`` wires them to a Rich console.
"""
from __future__ import annotations

import random
import time

from rich.console import Console

from ._engine import GameMeta, ask_line, header

# --- palette -----------------------------------------------------------------
TEAL = "#2dd4bf"
GOOD = "#9ece6a"
WARN = "#e0af68"
ERROR = "#f7768e"
SKY = "#7dd3fc"
MUTE = "#6b7089"

# --- tuning ------------------------------------------------------------------
ROUNDS = 8          # rounds before the final summary (q quits early)
BASE_POINTS = 100   # points for an instant, hint-free solve
FAST_SECS = 8.0     # solve within this for the full speed bonus
SLOW_SECS = 40.0    # past this, speed bonus is gone
HINT_COST = 25      # subtracted per revealed letter-hint
DEFN_COST = 15      # subtracted once for revealing the definition
STREAK_STEP = 20    # bonus added per consecutive solve (capped)
STREAK_CAP = 100    # max streak bonus

# --- word list: (word, hint) -------------------------------------------------
# 60+ themed words across tech, nature, and everyday vocabulary, each with a
# short definition / category used as the revealable hint.
WORDS: list[tuple[str, str]] = [
    ("python", "a high-level programming language (also a big snake)"),
    ("kernel", "the core of an operating system"),
    ("buffer", "a temporary holding area for data"),
    ("cursor", "the blinking marker showing your position"),
    ("lambda", "an anonymous, inline function"),
    ("syntax", "the grammar rules of a language"),
    ("binary", "base-2; ones and zeros"),
    ("vector", "a quantity with magnitude and direction"),
    ("thread", "a single sequence of execution"),
    ("socket", "an endpoint for network communication"),
    ("module", "a self-contained unit of code"),
    ("branch", "a divergent line of development in git"),
    ("commit", "a saved snapshot of changes"),
    ("docker", "a tool for packaging apps into containers"),
    ("tensor", "a multi-dimensional numerical array"),
    ("cipher", "a method of encrypting text"),
    ("schema", "the structure or blueprint of data"),
    ("pointer", "a variable holding a memory address"),
    ("compiler", "translates source code into machine code"),
    ("variable", "a named container for a value"),
    ("function", "a reusable block of code"),
    ("iterator", "an object that yields items one at a time"),
    ("closure", "a function bundled with its surroundings"),
    ("boolean", "a true-or-false value"),
    ("integer", "a whole number"),
    ("decorator", "wraps a function to extend its behavior"),
    ("generator", "yields values lazily, one at a time"),
    ("package", "a bundle of related modules"),
    ("library", "a collection of reusable code"),
    ("runtime", "the period when a program executes"),
    ("process", "a running instance of a program"),
    ("network", "interconnected computers that share data"),
    ("request", "a message asking a server for something"),
    ("response", "the server's reply to a request"),
    ("session", "a sustained interaction between client and server"),
    ("gateway", "a node that connects two networks"),
    ("protocol", "agreed rules for exchanging data"),
    ("database", "an organized collection of stored data"),
    ("frontend", "the user-facing part of an app"),
    ("backend", "the server-side part of an app"),
    ("monster", "a frightening imaginary creature"),
    ("wizard", "a magician in folklore"),
    ("dragon", "a legendary fire-breathing reptile"),
    ("castle", "a large fortified residence"),
    ("puzzle", "a problem designed to test ingenuity"),
    ("rhythm", "a strong, repeated pattern of sound"),
    ("galaxy", "a vast system of stars and dust"),
    ("planet", "a body orbiting a star"),
    ("comet", "an icy body with a glowing tail"),
    ("meteor", "a streak of light from space debris"),
    ("nebula", "a cloud of gas and dust in space"),
    ("gravity", "the force pulling objects together"),
    ("eclipse", "one body passing into another's shadow"),
    ("horizon", "where the earth seems to meet the sky"),
    ("glacier", "a slow-moving river of ice"),
    ("volcano", "a mountain that erupts molten rock"),
    ("canyon", "a deep gorge carved by a river"),
    ("forest", "a large area covered with trees"),
    ("desert", "a dry, sandy, barren region"),
    ("island", "land surrounded by water"),
    ("harbor", "a sheltered place for ships"),
    ("compass", "a tool that points toward north"),
    ("lantern", "a portable case for a light"),
    ("anchor", "a weight that holds a ship in place"),
    ("voyage", "a long journey by sea or space"),
    ("treasure", "a hoard of valuable things"),
    ("whisper", "to speak very softly"),
    ("shadow", "a dark shape cast by blocking light"),
    ("ember", "a small glowing piece of a fire"),
    ("frost", "a thin layer of ice crystals"),
    ("breeze", "a gentle wind"),
    ("thunder", "the loud sound after lightning"),
    ("crystal", "a solid with a regular geometric form"),
    ("velvet", "a soft, plush woven fabric"),
    ("orchid", "an exotic flowering plant"),
    ("falcon", "a swift bird of prey"),
    ("phoenix", "a mythical bird reborn from ashes"),
    ("jungle", "a dense tropical forest"),
    ("mosaic", "a picture made from small tiles"),
]


def scramble(word: str, rng: random.Random) -> str:
    """Return ``word`` with its letters shuffled using ``rng``.

    For words longer than one character the result is guaranteed to differ
    from the original. Words with no other distinct arrangement (e.g. all
    identical letters) fall back to returning the original unchanged.
    """
    if len(word) <= 1:
        return word
    chars = list(word)
    # If every char is identical there's no distinct scramble; bail out.
    if len(set(chars)) == 1:
        return word
    for _ in range(100):
        rng.shuffle(chars)
        candidate = "".join(chars)
        if candidate != word:
            return candidate
    # Extremely unlikely fallback: force a difference via a single swap.
    chars = list(word)
    for i in range(1, len(chars)):
        if chars[i] != chars[0]:
            chars[0], chars[i] = chars[i], chars[0]
            break
    return "".join(chars)


def is_correct(word: str, guess: str) -> bool:
    """True when ``guess`` matches ``word`` (case-insensitive, trimmed)."""
    return guess.strip().lower() == word.strip().lower()


# --- presentation helpers (pure-ish) -----------------------------------------

def _tiles(text: str, color: str) -> str:
    """Render ``text`` as spaced, uppercase Rich tiles in ``color``."""
    cells = "  ".join(ch.upper() for ch in text)
    return f"[bold {color}]{cells}[/bold {color}]"


def _round_score(elapsed: float, letter_hints: int, defn_shown: bool) -> int:
    """Points for a solve given time taken and hints used (never below 10)."""
    if elapsed <= FAST_SECS:
        speed = 1.0
    elif elapsed >= SLOW_SECS:
        speed = 0.25
    else:
        # linear falloff from 1.0 down to 0.25 across the window
        span = SLOW_SECS - FAST_SECS
        speed = 1.0 - 0.75 * (elapsed - FAST_SECS) / span
    pts = BASE_POINTS * speed
    pts -= letter_hints * HINT_COST
    if defn_shown:
        pts -= DEFN_COST
    return max(10, int(round(pts)))


def _streak_bonus(streak: int) -> int:
    """Bonus for the current streak (>=2 in a row), capped."""
    if streak < 2:
        return 0
    return min(STREAK_CAP, (streak - 1) * STREAK_STEP)


# --- interactive loop --------------------------------------------------------

def _play(console: Console) -> None:
    header(console, GAME)
    rng = random.Random()
    score = 0
    streak = 0
    best_streak = 0
    solved = 0
    skipped = 0
    missed = 0

    console.print(
        f"  [{MUTE}]Unscramble each word. Faster solves and longer streaks "
        f"score more.[/{MUTE}]"
    )
    console.print(
        f"  [{MUTE}]Commands:[/{MUTE}] "
        f"[{SKY}]hint[/{SKY}] [{MUTE}](definition){_thin()}then peel a "
        f"letter)[/{MUTE}]  "
        f"[{SKY}]skip[/{SKY}] [{MUTE}](give up)[/{MUTE}]  "
        f"[{SKY}]q[/{SKY}] [{MUTE}](quit)[/{MUTE}]\n"
    )

    # Sample distinct words so a single game never repeats one.
    pool = [w for w in WORDS]
    rng.shuffle(pool)
    n_rounds = min(ROUNDS, len(pool))

    for rnd in range(n_rounds):
        word, definition = pool[rnd]
        jumble = scramble(word, rng)

        letter_hints = 0      # how many leading letters revealed
        defn_shown = False
        start = time.monotonic()

        console.print(
            f"  [{MUTE}]Round {rnd + 1}/{n_rounds}[/{MUTE}]   "
            f"[{SKY}]score {score}[/{SKY}]   "
            f"[{WARN}]streak {streak}[/{WARN}]"
        )

        outcome = None  # "solved" | "skipped" | "missed" | "quit"

        while outcome is None:
            # Build the tile display, locking revealed leading letters in good.
            revealed = word[:letter_hints]
            tiles = _tiles(jumble, TEAL)
            extra = ""
            if letter_hints:
                extra = (
                    f"   [{MUTE}]starts with[/{MUTE}] "
                    f"{_tiles(revealed, GOOD)}"
                )
            console.print(
                f"\n  {tiles}   [{MUTE}]({len(word)} letters)[/{MUTE}]{extra}"
            )
            if defn_shown:
                console.print(f"  [{SKY}]hint:[/{SKY}] [{MUTE}]{definition}[/{MUTE}]")

            raw = ask_line(console, "unscramble:")
            low = raw.strip().lower()

            if low in ("q", "quit"):
                outcome = "quit"
                break
            if low == "skip":
                outcome = "skipped"
                break
            if low == "hint":
                if not defn_shown:
                    defn_shown = True
                    console.print(
                        f"  [{SKY}]hint cost −{DEFN_COST}[/{SKY}]"
                    )
                elif letter_hints < len(word) - 1:
                    letter_hints += 1
                    console.print(
                        f"  [{WARN}]revealed a letter (−{HINT_COST})[/{WARN}]"
                    )
                else:
                    console.print(
                        f"  [{MUTE}]no more hints — that would give it "
                        f"away![/{MUTE}]"
                    )
                continue
            if low == "":
                console.print(
                    f"  [{MUTE}]type a guess, or 'hint' / 'skip' / 'q'."
                    f"[/{MUTE}]"
                )
                continue

            if is_correct(word, raw):
                outcome = "solved"
            else:
                console.print(f"  [{ERROR}]✗ not quite — try again.[/{ERROR}]")

        elapsed = time.monotonic() - start

        if outcome == "quit":
            console.print(
                f"\n  [{MUTE}]The word was '{word}'. Wrapping up…[/{MUTE}]"
            )
            break

        if outcome == "solved":
            streak += 1
            best_streak = max(best_streak, streak)
            solved += 1
            gained = _round_score(elapsed, letter_hints, defn_shown)
            bonus = _streak_bonus(streak)
            score += gained + bonus
            bonus_txt = (
                f"  [{WARN}]+{bonus} streak×{streak}[/{WARN}]" if bonus else ""
            )
            console.print(
                f"  [bold {GOOD}]✓ '{word}'![/bold {GOOD}]  "
                f"[{GOOD}]+{gained}[/{GOOD}]{bonus_txt}  "
                f"[{MUTE}]({elapsed:.1f}s)[/{MUTE}]\n"
            )
        elif outcome == "skipped":
            streak = 0
            skipped += 1
            console.print(
                f"  [{WARN}]↪ skipped — it was '{word}'.[/{WARN}]\n"
            )
        else:  # missed (only reachable if loop is ever broken otherwise)
            streak = 0
            missed += 1
            console.print(
                f"  [{ERROR}]✗ it was '{word}'.[/{ERROR}]\n"
            )

    _summary(console, score, solved, skipped, best_streak)


def _summary(
    console: Console, score: int, solved: int, skipped: int, best_streak: int
) -> None:
    """Print the end-of-game scoreboard."""
    console.print(f"  [{MUTE}]{'─' * 34}[/{MUTE}]")
    console.print(f"  [bold {TEAL}]Final score: {score}[/bold {TEAL}]")
    console.print(
        f"  [{GOOD}]solved {solved}[/{GOOD}]   "
        f"[{WARN}]skipped {skipped}[/{WARN}]   "
        f"[{SKY}]best streak {best_streak}[/{SKY}]"
    )
    if score >= 700:
        tag = "Wordsmith supreme. 🏆"
    elif score >= 400:
        tag = "Sharp eyes!"
    elif score >= 150:
        tag = "Nicely done."
    else:
        tag = "Thanks for playing."
    console.print(f"  [{MUTE}]{tag}[/{MUTE}]\n")


def _thin() -> str:
    """A thin connective glyph for the command help line."""
    return " → "


GAME = GameMeta(
    key="scramble",
    name="Word Scramble",
    emoji="🔤",
    desc="unscramble the jumbled word",
    play=_play,
)
