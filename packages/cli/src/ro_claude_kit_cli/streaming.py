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

from typing import Any

from rich.console import Console

from ro_claude_kit_agent_patterns import Step


def _short(value: Any, limit: int = 80) -> str:
    s = str(value).replace("\n", " ")
    return s if len(s) <= limit else s[: limit - 1] + "…"


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
            # The todo tool gets a dedicated checklist render instead of a raw
            # tool line — this is the live plan tracker.
            if c.get("name") == "update_todos":
                from .todo import render_todos
                todos = (c.get("input") or {}).get("todos") or []
                render_todos(self.console, todos)
                return
            self.console.print(f"  [cyan]⚙[/cyan] [bold cyan]{c.get('name')}[/bold cyan] [dim]{_short(c.get('input'))}[/dim]")
        elif step.kind == "tool_result" and isinstance(c, dict):
            if c.get("name") == "update_todos":
                return  # the checklist was already drawn on the tool_call
            mark = "[red]✗[/red]" if c.get("is_error") else "[green]✓[/green]"
            self.console.print(f"  {mark} [dim]{_short(c.get('result', ''), 160)}[/dim]")
        elif step.kind == "error":
            self.console.print(f"  [red]⚠[/red] [dim]{_short(c, 200)}[/dim]")
        elif step.kind == "plan":
            self.console.print(f"  [magenta]🗂[/magenta] [dim]{_short(c, 200)}[/dim]")
        elif step.kind == "reflection":
            self.console.print(f"  [yellow]🔎[/yellow] [dim]{_short(c, 200)}[/dim]")

    def finish(self) -> None:
        """Call once the run is over to terminate any dangling streamed line."""
        self._flush_line()
