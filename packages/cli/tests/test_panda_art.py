"""Lock-in invariants for the ronin launch-panda activities.

The actual ASCII art is in ``panda_art.PANDA_ACTIVITIES`` and is allowed to
be tweaked — we just guarantee the properties the launch banner depends on:
the named activities exist, every activity has multiple frames, frame widths
within an activity stay constant (so the panel doesn't reflow as the panda
animates), and ``render_panda`` behaves on a non-TTY console (no animation,
prints panel + tagline by default, can skip the tagline).
"""
from __future__ import annotations

from io import StringIO

from rich.console import Console

from ro_claude_kit_cli import panda_art
from ro_claude_kit_cli.panda_art import (
    PANDA,
    PANDA_ACTIVITIES,
    PANDA_FRAMES,
    PANDA_NEUTRAL,
    normalize_frames,
    print_welcome_tagline,
    render_panda,
)


REQUIRED_ACTIVITIES = {"dancing", "running", "playing", "sleeping"}


def test_required_activities_exist():
    missing = REQUIRED_ACTIVITIES - set(PANDA_ACTIVITIES)
    assert not missing, f"missing activities: {missing}"


def test_every_activity_has_at_least_two_frames():
    for name, frames in PANDA_ACTIVITIES.items():
        assert len(frames) >= 2, f"{name}: needs ≥2 frames to animate"


def test_every_frame_shows_a_panda_face():
    # ʕ + ʔ are the panda kaomoji ears — sleeping uses ʕ-ᴥ-ʔ, others use ʕ•ᴥ•ʔ
    for name, frames in PANDA_ACTIVITIES.items():
        for i, frame in enumerate(frames):
            blob = "\n".join(frame)
            assert "ʕ" in blob and "ʔ" in blob, f"{name} frame {i} missing panda face"


def test_normalize_frames_pads_to_a_single_shape():
    for name, frames in PANDA_ACTIVITIES.items():
        normalized = normalize_frames(frames)
        row_counts = {len(f) for f in normalized}
        widths = {len(line) for f in normalized for line in f}
        assert len(row_counts) == 1, f"{name}: frame row count varies: {row_counts}"
        assert len(widths) == 1, f"{name}: frame line widths vary: {widths}"


def test_backward_compat_constants():
    # PANDA / PANDA_FRAMES are still exported for anything that imported them
    assert isinstance(PANDA, str) and PANDA
    assert isinstance(PANDA_FRAMES, tuple) and len(PANDA_FRAMES) >= 2
    assert all(isinstance(f, str) and f for f in PANDA_FRAMES)
    assert isinstance(PANDA_NEUTRAL, list)


def test_render_panda_non_tty_is_static_no_animation(monkeypatch):
    """On a non-TTY Console we render once (no rich.Live, no sleeping)."""
    def boom(*_a, **_kw):
        raise AssertionError("render_panda animated on a non-TTY console")

    monkeypatch.setattr(panda_art, "_animate", boom)

    buf = StringIO()
    console = Console(file=buf, force_terminal=False, width=140)
    render_panda(console)
    out = buf.getvalue()
    # panel content
    assert "ʕ" in out, "expected panda face in non-TTY render"
    assert "ronin" in out, "expected ronin version label"
    # tagline + hint (default behaviour)
    assert "One assistant for everything" in out
    assert "@path" in out and "/help" in out and "/q" in out


def test_render_panda_can_skip_tagline(monkeypatch):
    """`ronin panda` cycles activities and shouldn't repeat the welcome chrome."""
    monkeypatch.setattr(panda_art, "_animate", lambda *a, **kw: None)

    buf = StringIO()
    console = Console(file=buf, force_terminal=False, width=140)
    render_panda(console, activity="dancing", tagline=False)
    out = buf.getvalue()
    assert "ʕ" in out, "expected panda face"
    assert "One assistant for everything" not in out, "tagline should be suppressed"


def test_print_welcome_tagline_emits_the_expected_lines():
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, width=140)
    print_welcome_tagline(console)
    out = buf.getvalue()
    assert "One assistant for everything" in out
    assert "generate a panda image" in out
    assert "@path" in out and "/help" in out and "/q" in out
