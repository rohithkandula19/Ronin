"""The ronin launch panda — a small ASCII kaomoji panda that *does things*.

Each activity (dancing / running / playing / playing football / sleeping) has
four animation frames. On launch we pick a random activity and animate it in a
small rounded panel that mirrors the v0.17.4-style welcome card: panda
mascot + ``ronin vX.Y.Z · masterless Claude agent · <activity>`` line +
feature chips. The friendly tagline and quick-help hint render below the
panel.

On a non-TTY (pipe / CI / captured Console) we render a single still frame of
the neutral panda — no animation, no sleep loops.

The activities are exported as ``PANDA_ACTIVITIES`` so the ``ronin panda``
command can cycle through them on demand.
"""
from __future__ import annotations

import random
import sys
import time

from rich.console import Console


PANDA_NEUTRAL: list[str] = [
    " ʕ•ᴥ•ʔ ",
    "  (   ) ",
    "  ‾‾‾‾  ",
]


PANDA_ACTIVITIES: dict[str, list[list[str]]] = {
    "dancing": [
        [" ♪ \\ʕ•ᴥ•ʔ/", "    (   )  ", "   _/   \\_ "],
        ["    ƪʕ•ᴥ•ʔʅ ♪", "    (   )  ", "    \\   /  "],
        ["     \\ʕ•ᴥ•ʔ/ ♪", "    (   )  ", "   _/   \\_ "],
        [" ♪  ƪʕ•ᴥ•ʔʅ ", "    (   )  ", "    \\   /  "],
    ],
    "running": [
        [" »   ʕ•ᴥ•ʔ ", "    ε(   )϶", "     /   ⌐ "],
        [" »»  ʕ•ᴥ•ʔ ", "    ε(   )϶", "     ⌐   \\ "],
        [" »   ʕ•ᴥ•ʔ ", "    ε(   )϶", "     J   L "],
        [" »»  ʕ•ᴥ•ʔ ", "    ε(   )϶", "     L   J "],
    ],
    "playing": [
        [" ʕ•ᴥ•ʔﾉ      ●", "  (   )  ", "  /   \\  "],
        [" ʕ•ᴥ•ʔﾉ   ●  ", "  (   )  ", "  /   \\  "],
        [" ʕ•ᴥ•ʔﾉ ●   ",  "  (   )  ", "  /   \\  "],
        [" ʕ•ᴥ•ʔﾉ●     ", "  (   )  ", "  /   \\  "],
    ],
    "playing football": [
        [" ʕ•ᴥ•ʔ     ", "  (   )    ", "  /  L ●   "],
        [" ʕ•ᴥ•ʔ  ●  ", "  (   )    ", "  /   ⌐    "],
        [" ʕ•ᴥ•ʔ    ●", "  (   )    ", "  /   \\    "],
        [" ʕ•ᴥ•ʔ  ●  ", "  (   )    ", "  /   ⌐    "],
    ],
    "sleeping": [
        [" ʕ-ᴥ-ʔ   z ", "  (   )  ", "  ‾‾‾‾‾  "],
        [" ʕ-ᴥ-ʔ  Z  ", "  (   )  ", "  ‾‾‾‾‾  "],
        [" ʕ-ᴥ-ʔ zZ  ", "  (   )  ", "  ‾‾‾‾‾  "],
        [" ʕ-ᴥ-ʔ Z   ", "  (   )  ", "  ‾‾‾‾‾  "],
    ],
}


def normalize_frames(frames: list[list[str]]) -> list[list[str]]:
    """Pad every frame to the same row count and width so the panel doesn't
    reflow or resize as we cycle frames."""
    rows = max(len(f) for f in frames)
    width = max((len(line) for f in frames for line in f), default=0)
    out: list[list[str]] = []
    for f in frames:
        padded = [line.ljust(width) for line in f]
        padded += [" " * width] * (rows - len(padded))
        out.append(padded)
    return out


def _panel(frame_lines: list[str], activity: str):
    """One mascot frame + version/status/feature lines in a rounded teal panel."""
    from rich.align import Align
    from rich.panel import Panel

    from . import __version__
    from .theme import ACCENT

    mascot = "\n".join(f"[bold white]{line}[/bold white]" for line in frame_lines)
    body = (
        mascot
        + f"\n\n[bold {ACCENT}]ronin[/bold {ACCENT}] [dim]v{__version__}[/dim]"
        + f"  ·  [dim]masterless Claude agent[/dim]"
        + f"  ·  [green]{activity}[/green]\n"
        + "[dim]briefing · agent · code · chat · tui[/dim]"
    )
    return Panel.fit(Align.center(body), border_style=ACCENT, padding=(1, 3))


def print_welcome_tagline(console: Console) -> None:
    """The two lines under the panel — friendly tagline + slash-command hint."""
    from rich.text import Text

    from .theme import ACCENT, MUTE, SOFT

    tag = Text()
    tag.append("One assistant for everything", style=SOFT)
    tag.append(" — talk, ", style=MUTE)
    tag.append('"generate a panda image"', style=f"italic {ACCENT}")
    tag.append(", ", style=MUTE)
    tag.append('"write code to …"', style=f"italic {ACCENT}")
    tag.append(" (edits need approval), query your data, and more.", style=MUTE)

    hint = Text()
    hint.append("@path", style=f"bold {ACCENT}")
    hint.append(" to reference files  ·  ", style=MUTE)
    hint.append("/help", style=f"bold {ACCENT}")
    hint.append(" for commands  ·  ", style=MUTE)
    hint.append("/q", style=f"bold {ACCENT}")
    hint.append(" to quit.", style=MUTE)

    console.print(tag)
    console.print(hint)
    console.print()


def render_panda(
    console: Console,
    *,
    activity: str | None = None,
    loops: int = 2,
    tagline: bool = True,
) -> None:
    """Print the panda welcome.

    A random activity is picked unless ``activity`` is given. On a TTY we
    animate it for ``loops`` cycles via ``rich.Live``, settle on the first
    frame, then (by default) print the friendly tagline + slash-command hint
    below the panel. On a non-TTY we render one still frame of the neutral
    panda — no animation, no sleeping.

    Pass ``tagline=False`` when you only want the mascot panel (e.g. the
    ``ronin panda`` command, which cycles activities without re-printing the
    welcome chrome each time).
    """
    name = activity or random.choice(list(PANDA_ACTIVITIES))
    frames = normalize_frames(PANDA_ACTIVITIES[name])

    is_tty = bool(getattr(console, "is_terminal", False)) and sys.stdout.isatty()
    if is_tty:
        try:
            _animate(console, frames, name, loops)
        except Exception:  # pragma: no cover — fall back to a static print
            console.print(_panel(normalize_frames([PANDA_NEUTRAL])[0], "ready"))
    else:
        console.print(_panel(normalize_frames([PANDA_NEUTRAL])[0], "ready"))

    if tagline:
        print_welcome_tagline(console)


def _animate(console: Console, frames: list[list[str]], activity: str, loops: int) -> None:
    from rich.live import Live

    with Live(console=console, refresh_per_second=12, transient=False) as live:
        for _ in range(loops):
            for f in frames:
                live.update(_panel(f, activity))
                time.sleep(0.14)
        live.update(_panel(frames[0], activity))


# Backward-compat exports — anything that imported PANDA / PANDA_FRAMES still
# gets a sensible value. PANDA_FRAMES now holds the dancing frames joined into
# strings; PANDA is the neutral pose.
PANDA_FRAMES: tuple[str, ...] = tuple(
    "\n".join(frame) for frame in normalize_frames(PANDA_ACTIVITIES["dancing"])
)
PANDA: str = "\n".join(PANDA_NEUTRAL)
