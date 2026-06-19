"""Claude-Code-style bottom input box.

On a real terminal this uses **prompt_toolkit** to draw a clean, minimal input:
a ``›`` prompt with *ghost placeholder* text, a live ``/``-command and ``@``-file
dropdown (names + descriptions that filter as you type), and a transient hint
line with a right-aligned status. No persistent borders, so past turns read as
plain ``› your text`` in scrollback — Claude-Code-style flow. (The boxed,
pinned-input look lives in the full-screen TUI, ``ronin --tui``.) History (↑/↓)
and inline editing come for free.

If prompt_toolkit isn't available it falls back to a readline-backed bordered
prompt, and on anything that isn't a real TTY (pipes, tests) it falls back again
to a plain ``console.input`` so output stays clean and deterministic.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from rich.console import Console

from .theme import ACCENT, MUTE, SOFT

# readline powers the *fallback* prompt's history (↑/↓) and inline editing.
try:  # pragma: no cover - platform dependent
    import readline  # noqa: F401
except Exception:  # noqa: BLE001
    readline = None  # type: ignore

_completer_installed = False
_pt_session = None  # cached prompt_toolkit PromptSession (keeps in-memory history)
_vi_mode = False    # vi-style keybindings in the input box (toggled by /vim)

# Shift+Tab cycles the agent's edit mode (Claude Code's mode toggle):
#   normal       — gated edits, the agent asks before risky actions
#   auto-accept  — edits applied without prompting (yolo)
#   plan         — read-only: the agent explores + plans but never mutates files
_MODES = ("normal", "auto-accept", "plan")
_mode_idx = 0


def set_vi_mode(on: bool | None = None) -> bool:
    """Toggle (or set) vi keybindings for the prompt. Returns the new state.
    Applied to the live prompt_toolkit session on the next read."""
    global _vi_mode
    _vi_mode = (not _vi_mode) if on is None else bool(on)
    return _vi_mode


def current_mode() -> str:
    """The agent edit-mode selected via Shift+Tab — one of ``_MODES``."""
    return _MODES[_mode_idx]


def cycle_mode() -> str:
    """Advance to the next edit mode (wraps around). Returns the new mode."""
    global _mode_idx
    _mode_idx = (_mode_idx + 1) % len(_MODES)
    return _MODES[_mode_idx]


def set_mode(name: str) -> str:
    """Force a specific edit mode by name (no-op if unknown). Returns current."""
    global _mode_idx
    if name in _MODES:
        _mode_idx = _MODES.index(name)
    return current_mode()


# --------------------------------------------------------------------------- #
# slash-command catalogue (shared by both the pt dropdown and the readline tab) #
# --------------------------------------------------------------------------- #
def _slash_catalogue() -> dict[str, str]:
    """{command: description} for the slash menu. Falls back to a small static
    set if the code_mode module can't be imported (e.g. during early import)."""
    try:
        from .code_mode import SLASH_COMMANDS
        return dict(SLASH_COMMANDS)
    except Exception:  # noqa: BLE001
        return {
            "help": "show help", "login": "set provider + API key",
            "model": "show or switch the model", "models": "list models",
            "clear": "forget the conversation", "diff": "show the git diff",
            "undo": "revert the last file change", "memory": "show project memory",
            "init": "scaffold a RONIN.md", "tools": "list the agent's tools",
            "quit": "exit the session",
        }


# --------------------------------------------------------------------------- #
# readline fallback completer                                                   #
# --------------------------------------------------------------------------- #
def _slash_completions(text: str) -> list[tuple[str, int, str]]:
    """Pure (import-free) core of the ``/``-command dropdown — returned as
    ``(insert_text, start_position, display)`` rows so it's testable without a
    terminal. Only fires when the word starts with ``/`` or ``:`` and has no
    space yet (we complete the command token only)."""
    if not text or text[0] not in "/:":
        return []
    if " " in text:
        return []
    prefix, word = text[0], text[1:].lower()
    catalogue = _slash_catalogue()
    pad = max((len(n) for n in catalogue), default=8) + 2
    rows: list[tuple[str, int, str]] = []
    for name, desc in catalogue.items():
        if name.startswith(word):
            label = f"/{name}".ljust(pad + 1)
            rows.append((f"{prefix}{name}", -len(text), label + desc))
    return rows


_AT_MENTION_RE = re.compile(r"(?:^|\s)@([\w./\-]*)$")
_AT_IGNORE = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache",
              ".pytest_cache", ".ruff_cache", "dist", "build", ".next", ".turbo",
              ".idea", ".DS_Store"}
_AT_LIMIT = 60


def _at_completions(text: str, root: Path | str = ".") -> list[tuple[str, int, str]]:
    """Pure (import-free) core of the ``@``-file picker — returned as
    ``(insert_text, start_position, display)``.

    *Segment-wise* so it stays fast: it scans only the single directory the
    partial path points at (never the whole tree). Type ``@`` to list the root,
    ``@src/`` to list ``src/``, ``@src/co`` to filter inside ``src/``. Stays
    inside the project root."""
    m = _AT_MENTION_RE.search(text)
    if m is None:
        return []
    partial = m.group(1)                 # e.g. "src/co"  (may be "")
    start_position = -(len(partial) + 1)  # replace the whole "@partial" token
    root_path = Path(root).resolve()

    if "/" in partial:
        dirpart, base = partial.rsplit("/", 1)
        scan_dir = (root_path / dirpart).resolve()
        prefix = dirpart + "/"
    else:
        base = partial
        scan_dir = root_path
        prefix = ""

    # never escape the project root
    if not (scan_dir == root_path or root_path in scan_dir.parents):
        return []
    if not scan_dir.is_dir():
        return []

    base_low = base.lower()
    dirs: list[tuple[str, int, str]] = []
    files: list[tuple[str, int, str]] = []
    try:
        with os.scandir(scan_dir) as it:
            for entry in it:
                name = entry.name
                if name in _AT_IGNORE or (name.startswith(".") and not base.startswith(".")):
                    continue
                if base and not name.lower().startswith(base_low):
                    continue
                try:
                    is_dir = entry.is_dir()
                except OSError:
                    is_dir = False
                if is_dir:
                    dirs.append((f"@{prefix}{name}/", start_position, f"{name}/"))
                else:
                    files.append((f"@{prefix}{name}", start_position, name))
                if len(dirs) + len(files) >= _AT_LIMIT:
                    break
    except OSError:
        return []
    dirs.sort(key=lambda r: r[2].lower())
    files.sort(key=lambda r: r[2].lower())
    return dirs + files


def _slash_completer(text: str, state: int):
    """Readline-fallback tab-completion for /slash-commands."""
    if not text.startswith(("/", ":")):
        return None
    prefix = text[0]
    names = sorted(_slash_catalogue())
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


def read_prompt(
    console: Console,
    *,
    symbol: str = "›",
    hint: str = "",
    placeholder: str = "",
    status: str = "",
    root: Path | str = ".",
) -> str:
    """Read one line of input.

    On a real terminal: the prompt_toolkit Claude-Code-style prompt (ghost
    placeholder, live ``/`` dropdown, right-aligned ``status``). Falls back to a
    readline bordered prompt, then to ``console.input`` off-TTY so tests and
    piped runs behave exactly as before.
    """
    if not _interactive(console):
        return console.input("")

    # Preferred path: prompt_toolkit (the real Claude Code input experience).
    try:
        from prompt_toolkit import PromptSession  # noqa: F401
        _have_pt = True
    except Exception:  # noqa: BLE001
        _have_pt = False

    if _have_pt:
        try:
            return _pt_read(console, symbol=symbol, hint=hint,
                            placeholder=placeholder, status=status, root=root)
        except (EOFError, KeyboardInterrupt):
            raise  # let the REPL handle ⌃c / ⌃d
        except Exception:  # noqa: BLE001 - fall back, never break the REPL
            pass

    # Fallback: readline-backed bordered prompt.
    _install_completer()
    try:
        return _boxed_read(console, symbol=symbol, hint=hint)
    except Exception:  # noqa: BLE001
        return console.input(f"[bold {ACCENT}]{symbol}[/bold {ACCENT}] ")


# --------------------------------------------------------------------------- #
# prompt_toolkit path — the real Claude-Code-style input                        #
# --------------------------------------------------------------------------- #
def _pt_read(console: Console, *, symbol: str, hint: str,
             placeholder: str, status: str, root: Path | str = ".") -> str:
    global _pt_session
    from prompt_toolkit import PromptSession
    from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.history import FileHistory, InMemoryHistory
    from prompt_toolkit.styles import Style

    width = max(24, console.width)

    class _ReplCompleter(Completer):
        """Live dropdown for the REPL: ``/`` lists commands (name + description),
        ``@`` lists files segment-by-segment. Runs in a background thread (see
        ``complete_in_thread``) so typing never blocks on a directory scan."""

        def get_completions(self, document, complete_event):
            text = document.text_before_cursor
            for insert, start, display in _slash_completions(text):
                yield Completion(insert, start_position=start, display=display, display_meta="")
            for insert, start, display in _at_completions(text, root):
                yield Completion(insert, start_position=start, display=display, display_meta="")

    style = Style.from_dict({
        "border": ACCENT,
        "arrow": f"{ACCENT} bold",
        "placeholder": f"{MUTE} italic",
        "bottom-toolbar": f"noreverse bg:default {SOFT}",
        "bottom-toolbar.status": ACCENT,
        "bottom-toolbar.mode": f"{ACCENT} bold",
        "completion-menu": "bg:default",
        "completion-menu.completion": f"bg:default {SOFT}",
        "completion-menu.completion.current": f"bg:{ACCENT} #1a1a1a bold",
        "scrollbar.background": "bg:default",
        "scrollbar.button": f"bg:{MUTE}",
    })

    # Inline prompt by default (clean scrollback). RONIN_BOX=1 draws a
    # Claude-Code-style bordered box around the input.
    import os as _os
    box_on = _os.environ.get("RONIN_BOX", "1").strip().lower() not in {"0", "false", "no", "off"}
    if box_on:
        # Claude-Code style: a plain horizontal rule above the input, the input
        # itself, and a matching rule below (in the bottom toolbar). No corners,
        # no vertical bars - just two clean lines framing the prompt.
        _top = "─" * width
        message = [("class:border", _top + "\n"),
                   ("class:arrow", f"{symbol} ")]
    else:
        message = [("class:arrow", f"{symbol} ")]

    def bottom_toolbar():
        # A single transient hint line *below* the input — prompt_toolkit clears
        # the bottom toolbar on submit, so it never persists into scrollback. A
        # mode chip shows only when off the default (mirrors Claude Code).
        mode = current_mode()
        tag = ""
        if mode == "auto-accept":
            tag = "⏵⏵ auto-accept · "
        elif mode == "plan":
            tag = "⏸ plan mode · "
        left = hint or ""
        right = (f"● {status}" if status else "")
        gap = max(1, width - 2 - _w(tag) - _w(left) - _w(right))
        rows = [
            ("class:bottom-toolbar", "  "),
            ("class:bottom-toolbar.mode", tag),
            ("class:bottom-toolbar", left + " " * gap),
            ("class:bottom-toolbar.status", right),
        ]
        if box_on:
            _bot = "─" * width
            return [("class:border", _bot + "\n")] + rows
        return rows

    ph = [("class:placeholder", placeholder)] if placeholder else None

    if _pt_session is None:
        try:
            _hist_path = Path.home() / ".ronin" / "repl_history"
            _hist_path.parent.mkdir(parents=True, exist_ok=True)
            _history = FileHistory(str(_hist_path))
        except Exception:  # noqa: BLE001 - persistent history is best effort
            _history = InMemoryHistory()
        _pt_session = PromptSession(history=_history)

    # Collapse the boxed input on submit so scrollback stays clean — we echo a
    # plain ``› your text`` line below instead (Claude-Code-style history, where
    # past turns read as flat lines and only the *active* input wears the box).
    try:
        _pt_session.app.erase_when_done = True
    except Exception:  # noqa: BLE001
        pass

    # honour the /vim toggle for this (and every subsequent) read
    try:
        from prompt_toolkit.enums import EditingMode
        _pt_session.editing_mode = EditingMode.VI if _vi_mode else EditingMode.EMACS
    except Exception:  # noqa: BLE001
        pass

    # Shift+Tab cycles the edit mode and redraws the toolbar in place.
    kb = None
    try:
        from prompt_toolkit.key_binding import KeyBindings
        kb = KeyBindings()

        @kb.add("s-tab")
        def _(event):  # noqa: ANN001
            cycle_mode()
            event.app.invalidate()  # repaint the bottom toolbar with the new mode
    except Exception:  # noqa: BLE001
        kb = None

    text = _pt_session.prompt(
        message,
        placeholder=ph,
        completer=_ReplCompleter(),
        complete_while_typing=True,
        complete_in_thread=True,   # scan dirs off the UI thread → no typing lag
        auto_suggest=AutoSuggestFromHistory(),  # faint ghost suggestion you accept with right-arrow
        rprompt=None,
        bottom_toolbar=bottom_toolbar,
        style=style,
        reserve_space_for_menu=(0 if box_on else 8),
        key_bindings=kb,
    )
    # The box was erased on submit (erase_when_done); re-print the input as a flat
    # ``› your text`` line so scrollback reads like Claude Code's clean history.
    if text.strip():
        from rich.markup import escape as _esc
        console.print(f" [bold {ACCENT}]{symbol}[/bold {ACCENT}] {_esc(text)}")
    return text


def _w(text: str) -> int:
    """Display width of ``text`` (wide chars count as 2). Best-effort."""
    try:
        from prompt_toolkit.utils import get_cwidth
        return get_cwidth(text)
    except Exception:  # noqa: BLE001
        return len(text)


# --------------------------------------------------------------------------- #
# readline fallback prompt                                                      #
# --------------------------------------------------------------------------- #
def _boxed_read(console: Console, *, symbol: str, hint: str) -> str:
    """Minimal readline fallback (no prompt_toolkit) — matches the clean inline
    look: a ``›`` prompt with the hint dimmed below, no rules or borders.

         › your text (any length)
           hint…
    """
    line = console.input(f" [bold {ACCENT}]{symbol}[/bold {ACCENT}] ")
    if hint:
        console.print(f"  [{MUTE}]{hint}[/{MUTE}]")
    return line
