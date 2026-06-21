"""Opt-in **pinned-bottom** input bar for ronin's REPL.

This is a *fully additive*, *opt-in* alternative to the default inline prompt in
``prompt_box.py``. Nothing here runs unless something explicitly calls
``pinned_prompt`` (the REPL wires it behind ``RONIN_PINNED=1``), so the default
daily-driver path stays byte-for-byte unchanged.

Where the default prompt lets the input *flow* inline with scrollback (Claude
Code style), this draws a **status bar pinned to the very bottom** of the
terminal — ``model · NN% ctx · cwd`` — with the input line sitting just above it,
the way Aider / a full-screen TUI pins its footer. prompt_toolkit's
``bottom_toolbar`` does exactly this: it reserves the last row, repaints it as
the conversation scrolls, and clears it on submit so scrollback stays clean.

The layout math lives in **pure, deterministic helpers** (``status_bar`` /
``format_left``) so it can be unit-tested without a terminal. The interactive
entry point (``pinned_prompt``) is a thin prompt_toolkit shell around them and
is intentionally *not* unit-tested (it needs a live TTY).
"""
from __future__ import annotations

import sys

from .theme import ACCENT, MUTE, SOFT

# A sentinel returned (instead of raising) is *not* used here: like the
# prompt_toolkit path in ``prompt_box.read_prompt``, we let EOF / KeyboardInterrupt
# propagate so the REPL's own ``except (EOFError, KeyboardInterrupt)`` handles
# ⌃d / ⌃c exactly as it does for the default prompt.

__all__ = ["format_left", "status_bar", "pinned_prompt"]


# --------------------------------------------------------------------------- #
# PURE layout helpers (deterministic, unit-tested — no terminal, no I/O)        #
# --------------------------------------------------------------------------- #
def format_left(ctx_pct: int) -> str:
    """Format the context-left gauge, e.g. ``42`` → ``"42% ctx"``.

    The percentage is clamped to ``0..100`` so a stray over/underflow from the
    caller's token math can never produce a nonsensical bar. Pure + deterministic.
    """
    try:
        pct = int(ctx_pct)
    except (TypeError, ValueError):
        pct = 0
    pct = max(0, min(100, pct))
    return f"{pct}% ctx"


_SEP = " · "


def status_bar(model: str, ctx_pct: int, cwd: str, *, width: int = 80) -> str:
    """The formatted pinned status-bar text: ``model · NN% ctx · cwd``.

    Deterministic and terminal-free so it's unit-testable. Guarantees:

    * The result never exceeds ``width`` columns.
    * ``model`` and the context gauge (``NN% ctx``) are kept visible — when the
      line is too long it sheds the ``cwd`` first (truncated head-first with a
      leading ``…`` so the *tail*, the interesting part of a path, survives),
      and only as a last resort hard-truncates the whole line.
    """
    model = str(model)
    cwd = str(cwd)
    left = format_left(ctx_pct)
    width = max(1, int(width))

    # The essential prefix we always try to preserve: "model · NN% ctx".
    head = f"{model}{_SEP}{left}"

    full = f"{head}{_SEP}{cwd}" if cwd else head
    if len(full) <= width:
        return full

    # Too long. First, drop the cwd entirely if even "head · " doesn't fit.
    if len(head) >= width:
        # Can't even fit the head — hard-truncate it (model/ctx may be clipped,
        # but only in the degenerate case of an absurdly narrow width).
        return _ellipsize(head, width)

    if not cwd:
        return _ellipsize(head, width)

    # Room for "head · " + some cwd. Truncate the cwd head-first so the path tail
    # (the meaningful end) shows: e.g. "…/cli/ronin_cli".
    avail = width - len(head) - len(_SEP)
    if avail <= 0:
        return head  # head fits but no room for the separator+cwd → just the head
    return f"{head}{_SEP}{_ellipsize_head(cwd, avail)}"


def _ellipsize(text: str, width: int) -> str:
    """Truncate ``text`` to ``width`` columns, tail-first with a trailing ``…``."""
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width == 1:
        return "…"
    return text[: width - 1] + "…"


def _ellipsize_head(text: str, width: int) -> str:
    """Truncate ``text`` to ``width`` columns, **head-first** with a leading ``…``
    (keep the tail — for paths, the last segments are what matter)."""
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width == 1:
        return "…"
    return "…" + text[-(width - 1):]


# --------------------------------------------------------------------------- #
# Interactive pinned prompt (prompt_toolkit) — NOT unit-tested (needs a TTY)    #
# --------------------------------------------------------------------------- #
def pinned_prompt(
    message: str = "› ",
    *,
    model: str,
    ctx_pct: int,
    cwd: str,
    history=None,
):
    """Read **one line** with a status bar pinned to the bottom of the terminal.

    The input line is rendered with ``message`` as its prompt (e.g. ``"› "``);
    ``model · NN% ctx · cwd`` is drawn via prompt_toolkit's ``bottom_toolbar`` so
    it sticks to the last terminal row and is cleared on submit (keeping
    scrollback clean). Type-ahead is preserved — prompt_toolkit buffers
    keystrokes while it sets up.

    On EOF (⌃d) / KeyboardInterrupt (⌃c) this **propagates the exception**, just
    like the prompt_toolkit path in ``prompt_box.read_prompt`` — the REPL's
    surrounding ``except (EOFError, KeyboardInterrupt)`` decides what to do.

    ``history`` may be a prompt_toolkit ``History`` instance (e.g. a
    ``FileHistory``); when ``None`` an in-memory history is used for the session.
    """
    from prompt_toolkit import PromptSession
    from prompt_toolkit.formatted_text import HTML
    from prompt_toolkit.history import InMemoryHistory
    from prompt_toolkit.styles import Style

    # Width: best-effort from the real terminal; deterministic helper still caps it.
    width = _terminal_width()

    bar_text = status_bar(model, ctx_pct, cwd, width=width)

    style = Style.from_dict({
        "bottom-toolbar": f"noreverse bg:default {SOFT}",
        "bottom-toolbar.model": f"{ACCENT} bold",
        "bottom-toolbar.sep": MUTE,
        "bottom-toolbar.ctx": ACCENT,
        "bottom-toolbar.cwd": SOFT,
        "arrow": f"{ACCENT} bold",
    })

    def bottom_toolbar():
        # Re-evaluated by prompt_toolkit on every repaint, so the bar can track a
        # changing width if the terminal is resized mid-read.
        w = _terminal_width()
        return [("class:bottom-toolbar", " " + status_bar(model, ctx_pct, cwd, width=max(1, w - 1)))]

    session = PromptSession(history=history or InMemoryHistory())
    prompt_message = [("class:arrow", message)]
    # NOTE: deliberately NOT catching EOFError / KeyboardInterrupt — let them
    # propagate to the REPL, matching prompt_box.read_prompt's pt path.
    return session.prompt(
        prompt_message,
        bottom_toolbar=bottom_toolbar,
        style=style,
    )


def _terminal_width(default: int = 80) -> int:
    """Best-effort terminal width; falls back to ``default`` off a real TTY."""
    try:
        import shutil
        cols = shutil.get_terminal_size((default, 24)).columns
        return max(24, int(cols))
    except Exception:  # noqa: BLE001
        return default
