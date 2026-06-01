"""Live streaming renderer — the Claude-Code feel for ``ronin``.

The agent loop streams the model's text token-by-token via ``on_text`` and
reports tool activity via ``on_step``. ``LiveRenderer`` ties them together:

- on a real terminal, assistant text streams into a **live Markdown view** so
  **bold**, headings, bullet lists and ```code``` blocks render properly
  (syntax-highlighted) instead of showing raw ``**`` / ``#`` symbols,
- tool calls / results render as compact ``●`` / ``↳`` lines between text,
- on a non-terminal (pipes, tests) it falls back to plain inline streaming so
  output stays deterministic.

Because the answer streams inline, callers check ``streamed`` and skip
re-printing the final output in a panel.
"""
from __future__ import annotations

from rich.console import Console

from ro_claude_kit_agent_patterns import Step

from .theme import ACCENT, BULLET, CONNECTOR, ERR, MUTE, OK, SOFT, TOOL, short as _short


def _summarize_result(result) -> str:
    """A tidy one-line preview of a tool result: item counts for file listings,
    line counts for multi-line output, else a short snippet."""
    s = str(result).strip()
    if not s:
        return ""
    if s.startswith("[") and s.endswith("]"):
        import ast
        import json
        items = None
        for parse in (json.loads, ast.literal_eval):
            try:
                got = parse(s)
                if isinstance(got, list):
                    items = got
                    break
            except Exception:  # noqa: BLE001
                continue
        if items is not None:
            n = len(items)
            sample = ", ".join(str(x) for x in items[:6])
            more = f"  +{n - 6} more" if n > 6 else ""
            return f"{n} item{'s' if n != 1 else ''} · {_short(sample, 80)}{more}"
    lines = s.splitlines()
    if len(lines) > 1:
        return f"{len(lines)} lines · {_short(lines[0], 78)}"
    return _short(s, 100)


class LiveRenderer:
    """Streams assistant text (as live Markdown on a TTY) and renders tool steps
    as they happen, with a soft 'thinking…' spinner before the first token."""

    def __init__(self, console: Console) -> None:
        self.console = console
        self.streamed_text = False   # any assistant text streamed this run?
        self._status = None          # the thinking spinner (Rich Status)
        self._avatar_shown = False
        # terminal path: a live Markdown block accumulating the current text
        self._live = None
        self._buf = ""
        # non-terminal path: track an unterminated inline line
        self._dirty = False

    def _is_term(self) -> bool:
        return bool(getattr(self.console, "is_terminal", False))

    # --- thinking spinner ----------------------------------------------------
    def start(self) -> None:
        """Begin the soft 'thinking…' animation (only on a real terminal)."""
        if not self._is_term():
            return
        try:
            from rich.text import Text
            self._status = self.console.status(
                Text(" thinking…", style=SOFT), spinner="dots", spinner_style=ACCENT)
            self._status.start()
        except Exception:  # noqa: BLE001
            self._status = None

    def _stop_status(self) -> None:
        if self._status is not None:
            try:
                self._status.stop()
            except Exception:  # noqa: BLE001
                pass
            self._status = None

    # --- text block (live Markdown on a TTY) ---------------------------------
    def _markdown(self):
        from rich.markdown import Markdown
        return Markdown(self._buf, code_theme="monokai")

    def _end_text(self) -> None:
        """Finalise the current streamed block so tool lines / dividers follow it."""
        if self._live is not None:
            try:
                self._live.update(self._markdown())
                self._live.stop()
            except Exception:  # noqa: BLE001
                pass
            self._live = None
            self._buf = ""
        elif self._dirty:
            self.console.print()  # terminate a plain inline line
            self._dirty = False

    # --- agent hooks ---------------------------------------------------------
    def on_text(self, delta: str) -> None:
        self._stop_status()
        self.streamed_text = True

        if not self._is_term():
            # deterministic plain streaming for pipes/tests
            if not self._avatar_shown:
                self.console.print(BULLET)
                self._avatar_shown = True
            self.console.print(delta, end="", markup=False, highlight=False, soft_wrap=True)
            self._dirty = True
            return

        if self._live is None:
            if not self._avatar_shown:
                self.console.print(BULLET)
                self._avatar_shown = True
            from rich.live import Live
            self._buf = ""
            self._live = Live(self._markdown(), console=self.console,
                              refresh_per_second=8, vertical_overflow="visible",
                              transient=False)
            self._live.start()
        self._buf += delta
        self._live.update(self._markdown())

    def on_step(self, step: Step) -> None:
        # The model's text is streamed via on_text; don't reprint it here.
        if step.kind in ("thought", "final"):
            return
        self._stop_status()
        self._end_text()
        c = step.content
        if step.kind == "tool_call" and isinstance(c, dict):
            name = c.get("name", "")
            # The todo tool gets a dedicated checklist render (the live plan tracker).
            if name == "update_todos":
                from .todo import render_todos
                render_todos(self.console, (c.get("input") or {}).get("todos") or [])
                return
            from .theme import tool_label
            verb, target = tool_label(name, c.get("input"))
            tgt = f"[{MUTE}]({target})[/{MUTE}]" if target else ""
            self.console.print(f"{BULLET} [bold {TOOL}]{verb}[/bold {TOOL}]{tgt}")
        elif step.kind == "tool_result" and isinstance(c, dict):
            if c.get("name") == "update_todos":
                return  # the checklist was already drawn on the tool_call
            if c.get("is_error"):
                self.console.print(f"  [{ERR}]{CONNECTOR}  ✗ {_short(c.get('result', ''), 100)}[/{ERR}]")
            else:
                preview = _summarize_result(c.get("result", ""))
                if preview:
                    self.console.print(f"  [{MUTE}]{CONNECTOR}  {preview}[/{MUTE}]")
        elif step.kind == "error":
            self.console.print(f"  [{ERR}]{CONNECTOR}  ⚠ {_short(c, 160)}[/{ERR}]")
        elif step.kind == "plan":
            self.console.print(f"  [{ACCENT}]🗂 {_short(c, 160)}[/{ACCENT}]")
        elif step.kind == "reflection":
            self.console.print(f"  [{OK}]🔎 {_short(c, 160)}[/{OK}]")

    def finish(self) -> None:
        """Call once the run is over: stop the spinner and close the text block.
        No trailing rule — turns are separated by the ``⏺`` marker + the prompt,
        which keeps the scrollback clean and Claude-Code-like (no per-turn rules)."""
        self._stop_status()
        self._end_text()
