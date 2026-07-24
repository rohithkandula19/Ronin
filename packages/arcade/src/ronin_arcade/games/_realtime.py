"""Real-time terminal plumbing for the action games (Snake, 2048, …).

This gives the spatial games proper *arcade* controls: single keypresses
(arrow keys decoded, no Enter needed) and an in-place screen render that
repaints the same region every frame instead of spamming scrollback.

It uses POSIX raw/cbreak mode (macOS + Linux) via ``termios``. On Windows or a
non-TTY (pipes, tests) the helpers degrade safely: ``is_interactive()`` returns
False so callers can fall back to a typed prompt, and the key readers return a
quit signal rather than blocking forever.
"""
from __future__ import annotations

import sys

# Map raw escape sequences / control chars → friendly key names.
_ARROWS = {"\x1b[A": "up", "\x1b[B": "down", "\x1b[C": "right", "\x1b[D": "left",
           "\x1bOA": "up", "\x1bOB": "down", "\x1bOC": "right", "\x1bOD": "left"}


def is_interactive() -> bool:
    """True only on a real TTY where raw-mode key capture will work."""
    try:
        import termios  # noqa: F401
        return bool(sys.stdin.isatty() and sys.stdout.isatty())
    except Exception:  # noqa: BLE001 - termios missing (Windows) or no tty
        return False


class raw_mode:
    """Context manager putting the terminal in cbreak mode (keys arrive
    immediately, no echo) while keeping Ctrl-C working. No-op off a TTY."""

    def __init__(self) -> None:
        self.fd: int | None = None
        self.old = None

    def __enter__(self) -> "raw_mode":
        try:
            import termios
            import tty
            self.fd = sys.stdin.fileno()
            self.old = termios.tcgetattr(self.fd)
            tty.setcbreak(self.fd)
        except Exception:  # noqa: BLE001
            self.fd = None
        return self

    def __exit__(self, *exc) -> None:
        if self.fd is not None and self.old is not None:
            try:
                import termios
                termios.tcsetattr(self.fd, termios.TCSADRAIN, self.old)
            except Exception:  # noqa: BLE001
                pass


def _drain(maxn: int = 4) -> str:
    """Read any immediately-available bytes (the tail of an escape sequence)."""
    import select
    out = ""
    while len(out) < maxn:
        r, _, _ = select.select([sys.stdin], [], [], 0)
        if not r:
            break
        out += sys.stdin.read(1)
    return out


def _decode(ch: str) -> str:
    """Normalise a keypress to a name: up/down/left/right/enter/space/esc/q or
    the lowercased character."""
    if ch in ("\r", "\n"):
        return "enter"
    if ch == " ":
        return "space"
    if ch in ("\x03", "\x04"):   # Ctrl-C / Ctrl-D
        return "q"
    if ch == "\x1b":
        seq = ch + _drain()
        if seq == "\x1b":
            return "esc"
        return _ARROWS.get(seq, "esc")
    return ch.lower()


def read_key() -> str:
    """Block for one keypress and return its name. Establishes its own cbreak
    mode, so it is safe to call from a turn-based loop (e.g. 2048)."""
    if not is_interactive():
        return "q"
    with raw_mode():
        return _decode(sys.stdin.read(1))


def poll_key(timeout: float) -> str | None:
    """Wait up to ``timeout`` seconds for a key; return its name or ``None`` if
    none arrived. The caller must already be inside a :class:`raw_mode` block
    (used by real-time loops like Snake)."""
    import select
    try:
        r, _, _ = select.select([sys.stdin], [], [], timeout)
    except Exception:  # noqa: BLE001
        return None
    if not r:
        return None
    return _decode(sys.stdin.read(1))


# --- in-place screen rendering (ANSI) --------------------------------------
def clear() -> None:
    sys.stdout.write("\x1b[2J\x1b[H")
    sys.stdout.flush()


def home() -> None:
    """Move the cursor to the top-left without clearing — repaint in place."""
    sys.stdout.write("\x1b[H")


def draw(frame: str) -> None:
    """Repaint ``frame`` from the top-left. Lines are CR/LF-terminated so they
    render correctly while the terminal is in raw mode."""
    home()
    # \x1b[K clears each line's tail so shorter frames don't leave artifacts.
    body = "\r\n".join(line + "\x1b[K" for line in frame.split("\n"))
    sys.stdout.write(body + "\x1b[J")
    sys.stdout.flush()


def hide_cursor() -> None:
    sys.stdout.write("\x1b[?25l")
    sys.stdout.flush()


def show_cursor() -> None:
    sys.stdout.write("\x1b[?25h")
    sys.stdout.flush()


def enter_fullscreen() -> None:
    """Switch to the alternate screen buffer (a clean overlay) and hide the
    cursor — the game gets the whole screen; the prior scrollback is restored
    untouched by :func:`exit_fullscreen`."""
    sys.stdout.write("\x1b[?1049h\x1b[2J\x1b[H")
    hide_cursor()


def exit_fullscreen() -> None:
    """Restore the normal screen buffer and the cursor."""
    show_cursor()
    sys.stdout.write("\x1b[?1049l")
    sys.stdout.flush()
