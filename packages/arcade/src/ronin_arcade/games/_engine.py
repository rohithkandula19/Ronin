"""Shared contract for ronin's arcade games.

Every game is a module exposing a single ``GAME = GameMeta(...)`` value. The
``play`` callable runs the game interactively against a Rich ``Console``; the
game's *rules* live in pure functions next to it so they can be unit-tested
without a terminal. The ``ronin play`` command discovers games via the registry
in ``games/__init__.py``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from rich.console import Console


@dataclass(frozen=True)
class GameMeta:
    """One arcade game. ``key`` is the stable id used by ``ronin play <key>``."""
    key: str
    name: str
    emoji: str
    desc: str
    play: Callable[[Console], None]


def _theme_colors() -> tuple[str, str]:
    """ronin's brand colors when running inside the cli, safe fallbacks when the
    arcade is used standalone (ronin-arcade never *depends* on ronin-cli)."""
    try:
        from ronin_cli.theme import ACCENT, MUTE
        return ACCENT, MUTE
    except ImportError:
        return "#2dd4bf", "#6b7089"


def header(console: Console, game: "GameMeta") -> None:
    """A small consistent banner printed at the top of every game."""
    accent, mute = _theme_colors()
    console.print()
    console.print(f"  [bold {accent}]{game.emoji}  {game.name}[/bold {accent}]"
                  f"   [{mute}]{game.desc}[/{mute}]")
    console.print()


def ask_line(console: Console, prompt: str, *, default: str = "") -> str:
    """Read one line of input, tolerant of EOF/interrupt (returns ``default``)."""
    ACCENT, _ = _theme_colors()
    try:
        got = console.input(f"  [bold {ACCENT}]›[/bold {ACCENT}] {prompt} ").strip()
        return got or default
    except (EOFError, KeyboardInterrupt):
        return default
