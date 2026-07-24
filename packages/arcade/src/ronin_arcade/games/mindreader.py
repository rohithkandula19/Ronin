"""Mind Reader — think of anything; the panda reads your mind in 20 questions.

A flagship LLM game. The player silently thinks of *any* object, person, place,
or concept. The panda (whatever model ronin is configured to use) plays the
classic 20-questions guesser: it asks ONE concise yes/no question at a time,
listens to the player's ``y`` / ``n`` / ``maybe`` answer, and uses the running
Q&A transcript to narrow the field. When confident, it stops asking and makes a
guess; if the guess is right it wins ("I read your mind!"), otherwise it keeps
going until it runs out of questions and gracefully asks "what was it?".

All the model plumbing routes through ronin's own provider layer via
:mod:`._llm`, so this stays as provider-agnostic as the rest of the arcade and
never imports a vendor SDK. The two rules that actually need testing —
:func:`parse_answer` (player input → canonical answer) and :func:`is_guess`
(model line → "is this a final guess?") — are pure and I/O-free.
"""
from __future__ import annotations

from rich.console import Console

from ._engine import GameMeta, ask_line, header
from ._llm import available, complete

# --------------------------------------------------------------------------- #
# Palette (ronin soft pastels)
# --------------------------------------------------------------------------- #
TEAL = "#2dd4bf"   # accent
GOOD = "#9ece6a"   # win / yes
WARN = "#e0af68"   # maybe / soft warnings
ERR = "#f7768e"    # loss / no
SKY = "#7dd3fc"    # the panda's questions & guesses
MUTE = "#6b7089"   # secondary / scaffolding text

MAX_QUESTIONS = 20
GUESS_PREFIX = "GUESS:"

# --------------------------------------------------------------------------- #
# System prompt — instructs the model how to play.
# --------------------------------------------------------------------------- #
SYSTEM = (
    "You are a brilliant, playful panda playing 20 Questions. The human has "
    "thought of a single specific thing (an object, person, place, or "
    "concept) and you must guess it by asking yes/no questions.\n\n"
    "RULES:\n"
    "- Ask exactly ONE short yes/no question per turn. Keep it under 12 words.\n"
    "- Use the full conversation so far; never repeat a question already asked.\n"
    "- Start broad (alive? man-made? bigger than a car?) then narrow quickly.\n"
    "- The human answers 'yes', 'no', or 'maybe'. Treat 'maybe' as uncertain.\n"
    "- When you are reasonably confident, STOP asking and make your guess on a "
    "line that begins exactly with 'GUESS:' followed by your single best "
    "answer, e.g. 'GUESS: a giraffe'. Make a guess no later than question 20.\n"
    "- Output ONLY the question, or ONLY the 'GUESS:' line. No preamble, no "
    "explanations, no numbering, no extra lines."
)


# --------------------------------------------------------------------------- #
# Pure rules (unit-testable, no I/O)
# --------------------------------------------------------------------------- #
def parse_answer(raw: str) -> str:
    """Map a player's free-text reply to a canonical token.

    Returns one of ``"yes"``, ``"no"``, ``"maybe"``, ``"quit"``, or
    ``"unknown"``. Case-insensitive; accepts the short and long forms
    (``y``/``yes``, ``n``/``no``, ``m``/``maybe``, ``q``/``quit``). Common
    synonyms (``yeah``, ``nope``, ``sometimes``, ``exit``) are also understood.
    An empty / whitespace-only string counts as ``"quit"`` so a bare Enter
    bails out of the game cleanly.
    """
    s = (raw or "").strip().lower()
    if s == "":
        return "quit"
    if s in {"q", "quit", "exit", "stop"}:
        return "quit"
    if s in {"y", "yes", "yep", "yeah", "yup", "sure", "true", "correct", "right"}:
        return "yes"
    if s in {"n", "no", "nope", "nah", "false", "wrong", "incorrect"}:
        return "no"
    if s in {"m", "maybe", "sometimes", "kinda", "kind of", "sort of",
             "partly", "unsure", "dunno", "idk"}:
        return "maybe"
    # Fall back to first-letter heuristics for anything else short.
    first = s[0]
    if first == "y":
        return "yes"
    if first == "n":
        return "no"
    if first == "m":
        return "maybe"
    if first == "q":
        return "quit"
    return "unknown"


def is_guess(model_line: str) -> tuple[bool, str]:
    """Classify a model line as a final guess vs. a question.

    The model is instructed to prefix a guess with ``GUESS:``. If the line
    (after stripping) starts with that prefix (case-insensitive), this returns
    ``(True, <the guessed thing>)`` with the guess cleaned of the prefix and any
    surrounding punctuation/quotes. Otherwise returns ``(False, "")`` — it's a
    question (or blank).
    """
    line = (model_line or "").strip()
    if not line:
        return False, ""
    # Tolerate light markdown/bold the model might wrap around the prefix.
    probe = line.lstrip("*_> ").strip()
    if probe[:len(GUESS_PREFIX)].upper() == GUESS_PREFIX:
        thing = probe[len(GUESS_PREFIX):]
        # Peel any wrapping markdown / quotes / trailing punctuation from both
        # ends until the guess is clean (handles e.g. '**"the Eiffel Tower"**').
        wrap = " \t\"'`*_.!?"
        prev = None
        while thing != prev:
            prev = thing
            thing = thing.strip(wrap)
        return True, thing
    return False, ""


def first_line(text: str) -> str:
    """Collapse a model reply to its first non-empty line.

    Models occasionally add a stray blank line or trailing chatter; we only want
    the single question or the single ``GUESS:`` line. Pure helper.
    """
    for ln in (text or "").splitlines():
        ln = ln.strip()
        if ln:
            return ln
    return ""


# --------------------------------------------------------------------------- #
# Rendering helpers
# --------------------------------------------------------------------------- #
def _rules(console: Console) -> None:
    console.print(
        f"  [{MUTE}]Think of [bold {TEAL}]anything[/bold {TEAL}] — an object, a "
        f"person, a place, an idea. Don't tell me what it is.\n"
        f"  I'll ask up to [bold {TEAL}]{MAX_QUESTIONS}[/bold {TEAL}] yes/no "
        f"questions and try to read your mind.\n"
        f"  Answer [bold {GOOD}]y[/bold {GOOD}]es / [bold {ERR}]n[/bold {ERR}]o "
        f"/ [bold {WARN}]m[/bold {WARN}]aybe. Type [bold {TEAL}]q[/bold {TEAL}] "
        f"anytime to quit.[/]"
    )
    console.print()


def _blanked(console: Console) -> None:
    """Shown when the model returns nothing for a turn."""
    console.print(
        f"  [{WARN}]🔮 the panda blanked — try again?[/{WARN}]"
    )


def _build_history(qa: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Turn the running Q&A log into ``(role, content)`` turns for the model.

    Each asked question becomes an ``assistant`` turn; each player answer becomes
    a ``user`` turn. This gives the model the full transcript so it can reason
    about everything established so far.
    """
    history: list[tuple[str, str]] = []
    for question, answer in qa:
        history.append(("assistant", question))
        history.append(("user", answer))
    return history


def _next_line(qa: list[tuple[str, str]], asked: int) -> str:
    """Ask the model for its next move (a question or a ``GUESS:`` line)."""
    remaining = MAX_QUESTIONS - asked
    if remaining <= 1:
        # On the last turn, force a decision rather than another question.
        nudge = ("You are out of questions. Make your single best final guess "
                 "now on a line starting with 'GUESS:'.")
    else:
        nudge = (f"You have {remaining} questions left. Ask your next yes/no "
                 f"question, or make a 'GUESS:' if you're confident.")
    raw = complete(
        SYSTEM, nudge, max_tokens=80, history=_build_history(qa)
    )
    return first_line(raw)


# --------------------------------------------------------------------------- #
# Interactive loop
# --------------------------------------------------------------------------- #
def _play(console: Console) -> None:
    header(console, GAME)

    ok, reason = available()
    if not ok:
        console.print(
            f"  [{WARN}]🔮 Mind Reader needs a model configured — {reason}[/{WARN}]"
        )
        console.print()
        return

    _rules(console)
    ask_line(console, f"[{MUTE}]ready? press Enter when you've got something…[/]")
    console.print()

    qa: list[tuple[str, str]] = []   # [(question_asked, canonical_answer), ...]
    asked = 0

    while asked < MAX_QUESTIONS:
        line = _next_line(qa, asked)

        if not line:
            _blanked(console)
            cont = ask_line(console, f"[{MUTE}]try again? (Enter to retry, q to quit)[/]")
            if parse_answer(cont) == "quit":
                console.print(f"\n  [{MUTE}]No worries — we'll read minds another time.[/]\n")
                return
            continue

        guessing, thing = is_guess(line)

        # ------------------------------------------------------------------ #
        # The panda makes a guess.
        # ------------------------------------------------------------------ #
        if guessing:
            if not thing:
                # Empty guess — treat like a blank and retry.
                _blanked(console)
                continue
            console.print(
                f"  [bold {SKY}]🐼 Is it… [italic]{thing}[/italic]?[/bold {SKY}]"
            )
            raw = ask_line(console, f"[{TEAL}]did I get it? (y/n)[/]")
            verdict = parse_answer(raw)

            if verdict == "quit":
                console.print(f"\n  [{MUTE}]Bailing out — the mystery stays safe with you.[/]\n")
                return
            if verdict == "yes":
                console.print(
                    f"\n  [bold {GOOD}]🔮 I read your mind![/bold {GOOD}] "
                    f"[{MUTE}]Got it in {asked} question"
                    f"{'s' if asked != 1 else ''}.[/]\n"
                )
                return
            # A "no" (or anything else) — the guess was wrong; keep playing if
            # we still have questions left.
            console.print(f"  [{ERR}]Hmm, not it.[/{ERR}] [{MUTE}]Let me think…[/]\n")
            # Record the miss so the model won't guess it again.
            qa.append((f"{GUESS_PREFIX} {thing}", "no"))
            asked += 1
            if asked >= MAX_QUESTIONS:
                break
            continue

        # ------------------------------------------------------------------ #
        # The panda asks a question.
        # ------------------------------------------------------------------ #
        asked += 1
        console.print(
            f"  [{MUTE}]Q{asked}/{MAX_QUESTIONS}[/{MUTE}]  "
            f"[bold {SKY}]🐼 {line}[/bold {SKY}]"
        )
        raw = ask_line(console, f"[{TEAL}](y/n/m)[/]")
        ans = parse_answer(raw)

        if ans == "quit":
            console.print(f"\n  [{MUTE}]Game over — you out-thought the panda this time.[/]\n")
            return
        if ans == "unknown":
            # Don't burn a question on an unparseable answer; re-ask the same one.
            console.print(
                f"  [{WARN}]Just [bold {GOOD}]y[/bold {GOOD}] / "
                f"[bold {ERR}]n[/bold {ERR}] / [bold {WARN}]m[/bold {WARN}] "
                f"please.[/]"
            )
            asked -= 1
            continue

        label = {"yes": f"[{GOOD}]yes[/{GOOD}]",
                 "no": f"[{ERR}]no[/{ERR}]",
                 "maybe": f"[{WARN}]maybe[/{WARN}]"}[ans]
        console.print(f"     [{MUTE}]→ you said[/{MUTE}] {label}")
        console.print()
        qa.append((line, ans))

    # ----------------------------------------------------------------------- #
    # Out of questions — graceful loss.
    # ----------------------------------------------------------------------- #
    console.print(
        f"  [bold {WARN}]🐼 Okay, you stumped me![/bold {WARN}] "
        f"[{MUTE}]{MAX_QUESTIONS} questions and I couldn't crack it.[/]"
    )
    reveal = ask_line(console, f"[{TEAL}]what was it?[/]")
    reveal = (reveal or "").strip()
    if reveal and parse_answer(reveal) != "quit":
        console.print(
            f"\n  [{SKY}]Ahh — [italic]{reveal}[/italic]! "
            f"[/{SKY}][{MUTE}]I'll remember that one. Well played.[/]\n"
        )
    else:
        console.print(f"\n  [{MUTE}]Keeping it a secret? Fair enough — well played.[/]\n")


GAME = GameMeta(
    key="mindreader",
    name="Mind Reader",
    emoji="🔮",
    desc="think of anything — the panda reads your mind in 20 questions",
    play=_play,
)
