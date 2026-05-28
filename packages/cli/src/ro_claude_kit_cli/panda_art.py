"""The ronin launch welcome — a compact rounded panel with a small kaomoji
panda that dances, followed by the one-line tagline + quick-help hint.

We animate four 3-line kaomoji frames via rich.Live on a TTY, then settle on
the calm pose. On a non-TTY (pipe / CI / captured Console) the panel renders
once with the still frame and no animation.
"""
from __future__ import annotations

import sys
import time

from rich.console import Console

# Four dance frames: head stays put, arms wave. Each frame is 3 lines.
# We normalise widths at import time so the panel column stays steady as the
# panda dances.
PANDA_FRAMES: tuple[str, ...] = (
    # frame 0 — calm, arms by sides
    "   ʕ•ᴥ•ʔ\n"
    "  ε(   )ɜ\n"
    "     ||",
    # frame 1 — celebrating, both arms up
    "   ʕ•ᴥ•ʔ\n"
    "   \\( )/\n"
    "     /\\",
    # frame 2 — right arm up, left out
    "   ʕ•ᴥ•ʔ\n"
    "  ε(   )/\n"
    "    //",
    # frame 3 — left arm up, right out
    "   ʕ•ᴥ•ʔ\n"
    "  \\(   )ɜ\n"
    "    \\\\",
)


def _normalize(frames: tuple[str, ...]) -> tuple[str, ...]:
    """Right-pad every line of every frame to a single shared width so the
    panel doesn't reflow as we cycle frames."""
    width = max(len(line) for frame in frames for line in frame.splitlines())
    return tuple(
        "\n".join(line.ljust(width) for line in frame.splitlines())
        for frame in frames
    )


PANDA_FRAMES = _normalize(PANDA_FRAMES)

# Backward-compat constant — anything that imports PANDA gets the still pose.
_STILL_FRAME = 0
PANDA = PANDA_FRAMES[_STILL_FRAME]


def _card(frame_idx: int):
    """Build the welcome panel (panda + version line + feature chips)."""
    from rich.console import Group
    from rich.panel import Panel
    from rich.text import Text

    from . import __version__
    from .theme import ACCENT, MUTE, SOFT

    panda = Text(PANDA_FRAMES[frame_idx], style="grey85")
    version = Text()
    version.append("ronin ", style=f"bold {ACCENT}")
    version.append(f"v{__version__}", style=SOFT)
    version.append("  ·  ", style=MUTE)
    version.append("masterless Claude agent", style=SOFT)
    version.append("  ·  ", style=MUTE)
    version.append("running", style="green")
    tags = Text("briefing · agent · code · chat · tui", style=MUTE)

    body = Group(panda, Text(""), version, tags)
    return Panel(body, border_style=ACCENT, padding=(0, 2), expand=False)


def _print_tagline(console: Console) -> None:
    """The two lines under the panel: friendly tagline + slash-command hint."""
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


_LOOPS = 2          # cycle the 4 frames twice
_FRAME_DELAY = 0.18  # seconds per frame (~5.5 fps — readable kaomoji shimmy)


def _animate(console: Console) -> None:
    from rich.live import Live

    sequence = list(range(len(PANDA_FRAMES))) * _LOOPS + [_STILL_FRAME]
    with Live(
        _card(sequence[0]),
        console=console,
        refresh_per_second=10,
        transient=False,
    ) as live:
        for idx in sequence[1:]:
            time.sleep(_FRAME_DELAY)
            live.update(_card(idx))


def render_panda(console: Console) -> None:
    """Print the dancing-panda welcome panel + tagline.

    On a TTY we animate the kaomoji panda for ~1.6s and settle on the still
    pose. On a non-TTY (pipe, CI, captured Console) we render the still card
    once and no animation runs.
    """
    is_tty = bool(getattr(console, "is_terminal", False)) and sys.stdout.isatty()
    if is_tty:
        try:
            _animate(console)
            _print_tagline(console)
            return
        except Exception:  # pragma: no cover — fall back to static
            pass
    console.print(_card(_STILL_FRAME))
    _print_tagline(console)
