"""Lock-in invariants for the dancing kaomoji panda welcome panel.

The art is generated/edited in ``panda_art.PANDA_FRAMES``; we don't compare
exact glyph strings (kaomoji can be tweaked). We just guarantee the
properties the launch banner depends on: there are four frames, they all
settle to the same printable width (so the panel doesn't reflow as they
cycle), the static ``PANDA`` constant matches the still frame, and
``render_panda`` falls back to a single static render when stdout isn't a TTY.
"""
from __future__ import annotations

from io import StringIO

from rich.console import Console

from ro_claude_kit_cli import panda_art
from ro_claude_kit_cli.panda_art import (
    PANDA,
    PANDA_FRAMES,
    _STILL_FRAME,
    render_panda,
)


def _max_width(art: str) -> int:
    return max(len(line) for line in art.splitlines())


def test_four_dance_frames():
    assert len(PANDA_FRAMES) == 4
    for frame in PANDA_FRAMES:
        assert frame and isinstance(frame, str)


def test_frames_share_a_width_so_the_panel_does_not_reflow():
    widths = {_max_width(f) for f in PANDA_FRAMES}
    assert len(widths) == 1, f"frame widths diverge: {widths}"


def test_every_frame_shows_the_panda_face():
    # the ʕ ᴥ ʔ kaomoji bracket-mouth-bracket is the panda signature
    for i, frame in enumerate(PANDA_FRAMES):
        assert "ʕ" in frame and "ᴥ" in frame and "ʔ" in frame, (
            f"frame {i} missing panda face: {frame!r}"
        )


def test_still_constant_matches_still_frame():
    assert PANDA == PANDA_FRAMES[_STILL_FRAME]


def test_render_panda_non_tty_is_static_no_animation(monkeypatch):
    """On a non-TTY Console we render once (no rich.Live, no sleeping)."""
    def boom(*_a, **_kw):
        raise AssertionError("render_panda animated on a non-TTY console")

    monkeypatch.setattr(panda_art, "_animate", boom)

    buf = StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    render_panda(console)
    out = buf.getvalue()
    # the welcome panel includes the kaomoji face and the version label
    assert "ʕ" in out and "ᴥ" in out, "expected panda face in non-TTY render"
    assert "ronin" in out, "expected ronin version label in non-TTY render"
    # tagline + hint lines render too
    assert "One assistant for everything" in out
    assert "@path" in out and "/help" in out and "/q" in out
