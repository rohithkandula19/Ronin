"""Claude-Code-style bottom input box.

Draws a soft rounded border with a ``›`` prompt and lets you type *inside* the
box, with a faint hint line underneath — the signature Claude Code input look.
Arrow-key history and inline editing come from ``readline`` (stdlib).

On anything that isn't a real TTY (pipes, tests) it quietly falls back to a
plain ``console.input`` so output stays clean and deterministic.
"""
from __future__ import annotations

import sys

from rich.console import Console

from .theme import ACCENT, MUTE, SOFT

# Importing readline makes the builtin ``input()`` support history (↑/↓) and
# inline editing (←/→, ⌃a/⌃e, …) for free. Safe no-op if unavailable.
try:  # pragma: no cover - platform dependent
    import readline  # noqa: F401
except Exception:  # noqa: BLE001
    readline = None  # type: ignore

_completer_installed = False


def _slash_completer(text: str, state: int):
    """Tab-complete /slash-commands (only when the word starts with '/')."""
    if not text.startswith(("/", ":")):
        return None
    try:
        from .code_mode import SLASH_COMMANDS
        names = sorted(SLASH_COMMANDS)
    except Exception:  # noqa: BLE001
        names = ["help", "login", "model", "models", "clear", "diff", "undo",
                 "memory", "init", "tools", "quit"]
    prefix = text[0]
    matches = [f"{prefix}{n} " for n in names if f"{prefix}{n}".startswith(text)]
    return matches[state] if state < len(matches) else None


def _install_completer() -> None:
    global _completer_installed
    if _completer_installed or readline is None:
        return
    try:
        readline.set_completer(_slash_completer)
        readline.set_completer_delims(" \t\n")  # keep '/cmd' as one token
        # libedit (macOS default) vs GNU readline use different bind syntax
        if "libedit" in (getattr(readline, "__doc__", "") or ""):
            readline.parse_and_bind("bind ^I rl_complete")
        else:
            readline.parse_and_bind("tab: complete")
        _completer_installed = True
    except Exception:  # noqa: BLE001
        pass


def _interactive(console: Console) -> bool:
    try:
        return (
            getattr(console, "is_terminal", False)
            and sys.stdin.isatty()
            and sys.stdout.isatty()
        )
    except Exception:  # noqa: BLE001
        return False


def read_prompt(console: Console, *, symbol: str = "›", hint: str = "") -> str:
    """Read one line of input from a Claude-Code-style bordered box.

    Falls back to ``console.input`` when not attached to a real terminal so
    tests and piped runs behave exactly as before.
    """
    if not _interactive(console):
        return console.input("")

    _install_completer()  # tab-complete /commands
    try:
        return _boxed_read(console, symbol=symbol, hint=hint)
    except Exception:  # noqa: BLE001 - never let cosmetics break the REPL
        return console.input(f"[bold {ACCENT}]{symbol}[/bold {ACCENT}] ")


def _boxed_read(console: Console, *, symbol: str, hint: str) -> str:
    width = max(24, min(console.width, 100) - 2)   # inner width between the corners
    lead = f" {symbol} "                            # "│ › " minus the borders
    pad = " " * (width - len(lead))

    top = "╭" + "─" * width + "╮"
    bot = "╰" + "─" * width + "╯"

    console.print(f"[{ACCENT}]{top}[/{ACCENT}]")
    console.print(
        f"[{ACCENT}]│[/{ACCENT}][{SOFT}]{lead}[/{SOFT}]{pad}[{ACCENT}]│[/{ACCENT}]"
    )
    console.print(f"[{ACCENT}]{bot}[/{ACCENT}]")
    if hint:
        console.print(f"  [{MUTE}]{hint}[/{MUTE}]")

    # Hop the cursor back up into the box, just past "│ › ".
    up = 3 if hint else 2
    col = 1 + len(lead) + 1            # 1-based column right after the prompt symbol
    sys.stdout.write(f"\x1b[{up}A\x1b[{col}G")
    sys.stdout.flush()

    try:
        line = input("")
    finally:
        # Whatever happened (Enter, EOF, ⌃c), drop the cursor cleanly below the box.
        sys.stdout.write(f"\x1b[{up - 1}B\r")
        sys.stdout.flush()
    return line
