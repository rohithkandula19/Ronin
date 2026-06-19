"""AI Adventure — a living text adventure narrated live by the model.

Interactive fiction where the panda is your dungeon master. You pick a setting
(or roll a random one), the model writes a vivid opening scene, and from there
you type free-text actions — "search the desk", "draw my sword", "ask the ghost
who it was" — and the model narrates the consequences, keeping the thread of the
story alive through a running history. Each beat stays short and always ends on
an implicit *what now?*. Type ``quit`` to walk away; the panda gives you a small
send-off.

Unlike the other arcade games there's no board or score to test — instead the
*content* is pure: :data:`SETTINGS` and :func:`pick_setting` are I/O-free and
unit-testable, so the one bit of real logic (how a setting is chosen) can be
exercised without a terminal or a model. Every model call is funneled through
:func:`complete`, which never raises, so a dropped connection just stalls the
story rather than crashing the game.
"""
from __future__ import annotations

import random
from typing import List, Tuple

from rich.console import Console

from ._engine import GameMeta, ask_line, header
from ._llm import available, complete

# --------------------------------------------------------------------------- #
# Palette (ronin soft pastels)
# --------------------------------------------------------------------------- #
TEAL = "#2dd4bf"   # accent / the dungeon master's voice cue
GOOD = "#9ece6a"   # affirmations
WARN = "#e0af68"   # gentle warnings / no-model notice
ERR = "#f7768e"    # the story stalling
SKY = "#7dd3fc"    # the narration text itself
MUTE = "#6b7089"   # secondary text, echoed actions, turn counter

# --------------------------------------------------------------------------- #
# Settings — pure, unit-testable content
# --------------------------------------------------------------------------- #
#: A handful of evocative starting worlds. The player can pick by name at the
#: prompt, roll a random one, or invent their own — see :func:`pick_setting`.
SETTINGS: List[str] = [
    "a haunted spaceship adrift between dead stars",
    "an enchanted forest where the trees keep secrets",
    "a sunken city breathing at the bottom of the sea",
    "a clockwork castle that rebuilds itself every midnight",
    "a neon megacity slick with rain and bad debts",
    "a frozen monastery high on a screaming peak",
    "a pirate cove ruled by a one-eyed parrot king",
    "a desert of glass beneath two bleeding moons",
    "a sprawling library whose books bite back",
    "a carnival that only appears for travelers who are lost",
]


def pick_setting(choice: str, rng: random.Random) -> str:
    """Resolve the player's setting choice into a concrete world string.

    - Blank or the word ``"random"`` (any case) -> a random pick from
      :data:`SETTINGS` via the *passed* ``rng`` (so tests are deterministic).
    - Anything else -> the player's own text, trimmed (their imagination wins).

    Pure and I/O-free: the only randomness comes from the supplied
    ``random.Random``, never the module-global RNG.
    """
    text = (choice or "").strip()
    if not text or text.lower() == "random":
        return rng.choice(SETTINGS)
    return text


# --------------------------------------------------------------------------- #
# Dungeon-master persona
# --------------------------------------------------------------------------- #
SYSTEM = (
    "You are the Dungeon Master for a live, single-player text adventure. "
    "Write in vivid, sensory second person ('You ...'), present tense. "
    "Always ADVANCE the story in response to the player's action: show what "
    "happens, what they sense, and where it leaves them. Keep every reply SHORT "
    "— 3 to 5 sentences, no headers, no lists, no dice talk, no meta commentary. "
    "Introduce the occasional surprise, danger, character, or choice to keep "
    "momentum, and honor what came before so the world stays consistent. End "
    "each reply on a beat that quietly invites the next move (an open door, a "
    "watching figure, a sound) rather than literally asking 'what do you do?'. "
    "Never break character, never mention that you are an AI or a model, and "
    "keep everything PG-13."
)


def _opening_prompt(setting: str) -> str:
    """The first user turn: ask the DM to set the scene in the chosen world."""
    return (
        f"Begin a brand-new adventure set in {setting}. Write the opening scene "
        "in 3-4 sentences: drop the player into the world mid-moment, paint it "
        "with concrete sensory detail, hint at something intriguing nearby, and "
        "leave them poised to act. Do not summarize the rules or greet the "
        "player — just begin the story."
    )


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def _narrate(console: Console, text: str) -> None:
    """Render a chunk of model narration in the soft 'sky' story color.

    Wrapped in a quiet left margin to read like prose. Never raises.
    """
    console.print()
    for para in (text or "").split("\n"):
        line = para.strip()
        if line:
            console.print(f"  [{SKY}]{line}[/{SKY}]", soft_wrap=True)
        else:
            console.print()
    console.print()


def _echo_action(console: Console, action: str) -> None:
    """Show the player's typed action subtly, as a faint stage cue."""
    console.print(f"  [{MUTE}]» {action}[/{MUTE}]")


def _stall(console: Console) -> None:
    """Gentle notice when a completion comes back empty (no crash, no stack)."""
    console.print(
        f"\n  [{ERR}]The story stalls for a moment…[/{ERR}] "
        f"[{MUTE}]try that again, or type [bold {TEAL}]quit[/bold {TEAL}].[/]\n"
    )


# --------------------------------------------------------------------------- #
# Interactive loop
# --------------------------------------------------------------------------- #
_QUIT_WORDS = {"q", "quit", "exit", ""}


def _play(console: Console) -> None:
    header(console, GAME)

    ok, reason = available()
    if not ok:
        console.print(
            f"  [{WARN}]🗺️ AI Adventure needs a model configured — {reason}[/{WARN}]"
        )
        return

    console.print(
        f"  [{MUTE}]The panda is your dungeon master. Type whatever you want to "
        f"do in plain words —\n  [bold {TEAL}]open the door[/bold {TEAL}], "
        f"[bold {TEAL}]ask the stranger their name[/bold {TEAL}], "
        f"[bold {TEAL}]run[/bold {TEAL}]. Type "
        f"[bold {TEAL}]quit[/bold {TEAL}] to leave the tale.[/]"
    )
    console.print()

    # --- choose a world -------------------------------------------------- #
    console.print(f"  [{MUTE}]Where shall this story unfold? Name a setting, or "
                  f"press Enter for [bold {TEAL}]random[/bold {TEAL}].[/]")
    for s in SETTINGS[:5]:
        console.print(f"    [{MUTE}]· {s}[/{MUTE}]")
    console.print(f"    [{MUTE}]· …or invent your own.[/{MUTE}]")
    console.print()

    raw_choice = ask_line(console, "setting:")
    setting = pick_setting(raw_choice, random.Random())
    console.print(
        f"\n  [bold {TEAL}]The tale begins in[/bold {TEAL}] "
        f"[{GOOD}]{setting}[/{GOOD}][{MUTE}].[/]"
    )

    # --- opening scene --------------------------------------------------- #
    history: List[Tuple[str, str]] = []
    opening_request = _opening_prompt(setting)
    scene = complete(SYSTEM, opening_request, max_tokens=400)
    if not scene:
        _stall(console)
        console.print(
            f"  [{MUTE}]The panda couldn't reach the realms just now. "
            f"Maybe later, traveler.[/]\n"
        )
        return

    _narrate(console, scene)
    # Seed history with the opening exchange so continuity is preserved.
    history.append(("user", opening_request))
    history.append(("assistant", scene))

    # --- main loop ------------------------------------------------------- #
    turns = 0
    while True:
        action = ask_line(console, "> ")
        low = action.strip().lower()

        if low in _QUIT_WORDS:
            _wrap_up(console, history, turns)
            return

        _echo_action(console, action)
        turns += 1

        narration = complete(
            SYSTEM,
            f"The player does: {action}\n\nNarrate what happens next.",
            max_tokens=400,
            history=history,
        )
        if not narration:
            _stall(console)
            # Don't pollute history with a failed beat; let them retry.
            turns -= 1
            continue

        _narrate(console, narration)
        history.append(("user", action))
        history.append(("assistant", narration))


def _wrap_up(console: Console, history: List[Tuple[str, str]], turns: int) -> None:
    """A small send-off on quit: ask the DM to land the story, then sign off.

    Falls back to a plain farewell if there was no story yet or the model is
    unreachable — never crashes.
    """
    if turns > 0 and history:
        epilogue = complete(
            SYSTEM,
            "The player is ending their journey here. In 2-3 sentences, bring "
            "this adventure to a graceful, satisfying close — a final image that "
            "honors where they ended up. Do not ask any further questions.",
            max_tokens=200,
            history=history,
        )
        if epilogue:
            _narrate(console, epilogue)

    console.print(
        f"  [bold {TEAL}]The tale folds shut.[/bold {TEAL}] "
        f"[{MUTE}]You wandered for[/{MUTE}] "
        f"[bold {GOOD}]{turns}[/bold {GOOD}] "
        f"[{MUTE}]turn{'s' if turns != 1 else ''}. Until the next story.[/]\n"
    )


GAME = GameMeta(key="adventure", name="AI Adventure", emoji="🗺️",
                desc="a living text adventure — the panda is your dungeon master",
                play=_play)
