"""Lock-in invariants for the dancing launch panda.

We don't compare exact glyph strings — the art is generated from shapes by
``packages/cli/tools/build_panda_face.py`` and can be regenerated. We just
guarantee the properties the launch banner depends on: there are four frames,
they all settle to the same printable width (so the lockup doesn't jitter as
they cycle), the static ``PANDA`` constant matches the still frame, and
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


def test_frames_share_a_width_so_the_lockup_does_not_jitter():
    widths = {_max_width(f) for f in PANDA_FRAMES}
    # all frames must render at the same column width
    assert len(widths) == 1, f"frame widths diverge: {widths}"


def test_still_constant_matches_still_frame():
    assert PANDA == PANDA_FRAMES[_STILL_FRAME]


def test_render_panda_non_tty_is_static_no_animation(monkeypatch):
    """On a non-TTY Console we render once (no rich.Live, no sleeping)."""
    # Sentinel: if _animate is reached on a non-TTY, the test fails loudly.
    def boom(*_a, **_kw):
        raise AssertionError("render_panda animated on a non-TTY console")

    monkeypatch.setattr(panda_art, "_animate", boom)

    buf = StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    render_panda(console)
    out = buf.getvalue()
    # the still frame's middle row appears in the static output
    signature_row = PANDA_FRAMES[_STILL_FRAME].splitlines()[3]
    # rich may pad/wrap — match on a distinctive substring
    needle = signature_row.strip().lstrip("▀").rstrip("▀")[:20]
    assert needle in out, "expected static still frame in non-TTY render"
