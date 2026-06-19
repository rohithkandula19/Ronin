"""AI Trivia — endless, LLM-generated multiple-choice trivia, ronin's quiz arcade.

Pick a category ("science", "movies", "history", … or just hit enter for *surprise
me*) and a difficulty, then play forever: each round the model invents ONE fresh
four-option question, you answer A/B/C/D, and the panda reveals whether you nailed
it plus a short fun fact. Score and streak ride along until you quit.

Questions route through ronin's own provider layer (:mod:`._llm`), so the game runs
on whatever backend you've configured. The model is asked for STRICT JSON and the
returned text is run through :func:`parse_question`, a defensive parser that digs
the JSON out of any surrounding prose / ``` fences and validates it. The parser and
:func:`check` are pure and I/O-free so they can be unit-tested without a terminal,
a network, or a model.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from rich.console import Console

from ._engine import GameMeta, ask_line, header
from ._llm import available, complete

# --------------------------------------------------------------------------- #
# Palette (ronin soft pastels)
# --------------------------------------------------------------------------- #
TEAL = "#2dd4bf"   # accent
GOOD = "#9ece6a"   # correct answer
ERR = "#f7768e"    # wrong answer
WARN = "#e0af68"   # warnings / model unavailable
SKY = "#7dd3fc"    # option letters / info
MUTE = "#6b7089"   # secondary text

LETTERS = ("A", "B", "C", "D")


# --------------------------------------------------------------------------- #
# Pure rules (unit-testable — no I/O, never raise)
# --------------------------------------------------------------------------- #
def _slice_json(text: str) -> Optional[str]:
    """Return the first balanced ``{...}`` block in ``text``, or None.

    Scans for the first ``{`` and walks forward tracking brace depth, ignoring
    braces that live inside double-quoted strings (with escape handling) so a
    ``}`` inside a value can't close the object early. Returns the substring
    including both braces, or None if no balanced block is found.
    """
    if not text:
        return None
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def parse_question(text: str) -> Optional[Dict[str, Any]]:
    """Extract and validate a trivia question from raw model output.

    Tolerates the model wrapping the JSON in prose or ```json fences: it pulls the
    first balanced ``{...}`` block and ``json.loads`` it. A valid result must have a
    non-empty ``question`` string, an ``options`` mapping with non-empty A/B/C/D
    entries, an ``answer`` that is one of A-D, and a ``fact`` string. Returns a
    normalized dict (``answer`` upper-cased, ``fact`` defaulted to "") or None on
    *any* problem — never raises.
    """
    block = _slice_json(text if isinstance(text, str) else "")
    if block is None:
        return None
    try:
        data = json.loads(block)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None

    question = data.get("question")
    if not isinstance(question, str) or not question.strip():
        return None

    raw_options = data.get("options")
    if not isinstance(raw_options, dict):
        return None
    options: Dict[str, str] = {}
    for letter in LETTERS:
        # accept either upper- or lower-case keys from the model
        val = raw_options.get(letter, raw_options.get(letter.lower()))
        if not isinstance(val, str) or not val.strip():
            return None
        options[letter] = val.strip()

    answer = data.get("answer")
    if not isinstance(answer, str):
        return None
    answer = answer.strip().upper()
    if answer not in LETTERS:
        return None

    fact = data.get("fact")
    if not isinstance(fact, str):
        fact = ""

    return {
        "question": question.strip(),
        "options": options,
        "answer": answer,
        "fact": fact.strip(),
    }


def check(parsed: Dict[str, Any], choice: str) -> bool:
    """Return True iff ``choice`` (a letter, case-insensitive) is the answer."""
    try:
        picked = str(choice).strip().upper()
        return bool(picked) and picked == str(parsed["answer"]).strip().upper()
    except (KeyError, TypeError, AttributeError):
        return False


# --------------------------------------------------------------------------- #
# Prompt construction
# --------------------------------------------------------------------------- #
_SYSTEM = (
    "You are a sharp, playful trivia host. You write ONE multiple-choice trivia "
    "question at a time with exactly four options. Exactly one option is correct. "
    "Keep questions factually accurate and unambiguous, options plausible and "
    "roughly the same length, and add a short, genuinely interesting fun fact about "
    "the answer. Reply with STRICT JSON ONLY — no prose, no markdown, no code "
    "fences — in exactly this shape:\n"
    '{"question": "...", "options": {"A": "...", "B": "...", "C": "...", '
    '"D": "..."}, "answer": "B", "fact": "..."}\n'
    'The "answer" must be one of A, B, C, or D.'
)


def _build_prompt(category: str, difficulty: str, asked: List[str]) -> str:
    """Compose the per-round user prompt, listing prior questions to avoid repeats."""
    cat = category.strip() or "any topic — surprise me, pick something unexpected"
    diff = difficulty.strip() or "medium"
    lines = [
        f"Category: {cat}",
        f"Difficulty: {diff}",
        "Write a brand-new question. Vary the subject, era, and phrasing each time, "
        "and shuffle which letter holds the correct answer.",
    ]
    if asked:
        recent = asked[-12:]
        joined = "\n".join(f"- {q}" for q in recent)
        lines.append(
            "Do NOT repeat or closely paraphrase any of these already-asked "
            f"questions:\n{joined}"
        )
    lines.append("Respond with the strict JSON object only.")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def _render_question(console: Console, parsed: Dict[str, Any], number: int) -> None:
    """Print the question and its four colored options."""
    console.print(f"  [bold {TEAL}]Q{number}.[/bold {TEAL}] {parsed['question']}")
    console.print()
    for letter in LETTERS:
        console.print(
            f"     [bold {SKY}]{letter}[/bold {SKY}][{MUTE}])[/{MUTE}] "
            f"{parsed['options'][letter]}"
        )
    console.print()


def _reveal(console: Console, parsed: Dict[str, Any], picked: str,
            correct: bool) -> None:
    """Show whether the pick was right, the correct option, and the fun fact."""
    ans = parsed["answer"]
    ans_text = parsed["options"][ans]
    if correct:
        console.print(
            f"  [bold {GOOD}]✓ Correct![/bold {GOOD}] "
            f"[{MUTE}]{ans}) {ans_text}[/{MUTE}]"
        )
    else:
        shown = f"{picked.upper()}) " if picked.strip() else ""
        console.print(
            f"  [bold {ERR}]✗ Nope[/bold {ERR}] "
            f"[{MUTE}]— you said {shown}[/{MUTE}]"
            f"[{ERR}]·[/{ERR}] "
            f"[{MUTE}]answer was[/{MUTE}] "
            f"[bold {GOOD}]{ans}) {ans_text}[/bold {GOOD}]"
        )
    if parsed.get("fact"):
        console.print(f"    [{SKY}]✦[/{SKY}] [{MUTE}]{parsed['fact']}[/{MUTE}]")
    console.print()


# --------------------------------------------------------------------------- #
# Interactive loop
# --------------------------------------------------------------------------- #
def _play(console: Console) -> None:
    header(console, GAME)

    ok, reason = available()
    if not ok:
        console.print(f"[{WARN}]🎓 AI Trivia needs a model configured — {reason}[/{WARN}]")
        return

    console.print(
        f"  [{MUTE}]Answer with [bold {TEAL}]A[/bold {TEAL}], "
        f"[bold {TEAL}]B[/bold {TEAL}], [bold {TEAL}]C[/bold {TEAL}], or "
        f"[bold {TEAL}]D[/bold {TEAL}]. Type "
        f"[bold {TEAL}]q[/bold {TEAL}] anytime to quit.[/]"
    )
    console.print()

    category = ask_line(
        console, "Category (e.g. science, movies, history — blank = surprise me):")
    if category.strip().lower() in ("q", "quit"):
        return
    difficulty = ask_line(
        console, "Difficulty (easy / medium / hard — blank = medium):")
    if difficulty.strip().lower() in ("q", "quit"):
        return

    shown_cat = category.strip() or "surprise me"
    shown_diff = difficulty.strip() or "medium"
    console.print(
        f"\n  [{MUTE}]Category[/{MUTE}] [bold {TEAL}]{shown_cat}[/bold {TEAL}] "
        f"[{MUTE}]· difficulty[/{MUTE}] [bold {TEAL}]{shown_diff}[/bold {TEAL}]\n"
    )

    asked: List[str] = []
    score = 0
    rounds = 0
    streak = 0
    best_streak = 0

    while True:
        rounds += 1
        console.print(
            f"  [{MUTE}]── score [bold {TEAL}]{score}[/bold {TEAL}] "
            f"· streak [bold {WARN}]{streak}[/bold {WARN}] ──[/{MUTE}]"
        )
        console.print(f"  [{MUTE}]thinking…[/{MUTE}]")

        raw = complete(_SYSTEM, _build_prompt(category, difficulty, asked),
                       max_tokens=400)
        parsed = parse_question(raw)

        if parsed is None:
            console.print(f"  [{WARN}]couldn't get a question — try again?[/{WARN}]")
            again = ask_line(console, "press enter to retry, or q to quit:")
            if again.strip().lower() in ("q", "quit"):
                rounds -= 1
                break
            rounds -= 1
            console.print()
            continue

        asked.append(parsed["question"])
        console.print()
        _render_question(console, parsed, rounds)

        raw_choice = ask_line(console, "your answer (A/B/C/D):")
        low = raw_choice.strip().lower()
        if low in ("q", "quit", ""):
            rounds -= 1
            asked.pop()
            break
        if low not in ("a", "b", "c", "d"):
            console.print(
                f"  [{WARN}]Pick A, B, C, or D — that counts as a miss.[/{WARN}]"
            )
            streak = 0
            _reveal(console, parsed, "", False)
            continue

        correct = check(parsed, low)
        if correct:
            score += 1
            streak += 1
            best_streak = max(best_streak, streak)
        else:
            streak = 0
        _reveal(console, parsed, low, correct)

    if rounds > 0:
        pct = int(round(100 * score / rounds)) if rounds else 0
        console.print(
            f"  [bold {TEAL}]Game over.[/bold {TEAL}] "
            f"[{MUTE}]You scored[/{MUTE}] "
            f"[bold {GOOD}]{score}[/bold {GOOD}]"
            f"[{MUTE}]/{rounds} ({pct}%)[/{MUTE}] "
            f"[{MUTE}]· best streak[/{MUTE}] "
            f"[bold {WARN}]{best_streak}[/bold {WARN}][{MUTE}].[/{MUTE}]\n"
        )
    else:
        console.print(f"  [{MUTE}]No questions answered — come back smarter. 🐼[/{MUTE}]\n")


GAME = GameMeta(key="trivia", name="AI Trivia", emoji="🎓",
                desc="endless AI-generated trivia — beat the panda", play=_play)
