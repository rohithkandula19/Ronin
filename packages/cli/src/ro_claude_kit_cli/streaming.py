"""Live streaming renderer — the Claude-Code feel for ``ronin code``/``agent``.

The agent loop streams the model's text token-by-token via ``on_text`` and
reports tool activity via ``on_step``. ``LiveRenderer`` ties the two together:

- assistant text prints inline as it arrives (no waiting for the whole turn),
- tool calls / results render as compact, indented lines between text,
- the duplicate ``thought`` / ``final`` steps (whose text already streamed) are
  suppressed so nothing prints twice.

Because the answer streams inline, callers check ``streamed`` and skip
re-printing the final output in a panel.
"""
from __future__ import annotations

from rich.console import Console

from ro_claude_kit_agent_patterns import Step

from .theme import ACCENT, BULLET, ERR, MUTE, OK, TOOL, short as _short


class LiveRenderer:
    """Streams assistant text and renders tool steps as they happen."""

    def __init__(self, console: Console) -> None:
        self.console = console
        self._dirty = False        # text written to the current line, no newline yet
        self.streamed_text = False  # any assistant text streamed this run?

    def _flush_line(self) -> None:
        if self._dirty:
            self.console.print()  # terminate the streamed line
            self._dirty = False

    # --- agent hooks ---------------------------------------------------------
    def on_text(self, delta: str) -> None:
        # markup=False so model output like "[x]" can't blow up Rich markup.
        self.console.print(delta, end="", markup=False, highlight=False, soft_wrap=True)
        self._dirty = True
        self.streamed_text = True

    def on_step(self, step: Step) -> None:
        # The model's text is streamed via on_text; don't reprint it here.
        if step.kind in ("thought", "final"):
            return
        self._flush_line()
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
            tgt = f" [{MUTE}]{target}[/{MUTE}]" if target else ""
            self.console.print(f"{BULLET} [bold {TOOL}]{verb}[/bold {TOOL}]{tgt}")
        elif step.kind == "tool_result" and isinstance(c, dict):
            if c.get("name") == "update_todos":
                return  # the checklist was already drawn on the tool_call
            preview = _short(c.get("result", ""), 100)
            if c.get("is_error"):
                self.console.print(f"  [{ERR}]└ ✗ {preview}[/{ERR}]")
            elif preview:
                self.console.print(f"  [{MUTE}]└ {preview}[/{MUTE}]")
        elif step.kind == "error":
            self.console.print(f"  [{ERR}]⚠ {_short(c, 160)}[/{ERR}]")
        elif step.kind == "plan":
            self.console.print(f"  [{ACCENT}]🗂 {_short(c, 160)}[/{ACCENT}]")
        elif step.kind == "reflection":
            self.console.print(f"  [{OK}]🔎 {_short(c, 160)}[/{OK}]")

    def finish(self) -> None:
        """Call once the run is over to terminate any dangling streamed line."""
        self._flush_line()
