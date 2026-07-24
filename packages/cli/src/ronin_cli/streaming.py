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

import re

from rich.console import Console
from rich.markup import escape as _esc

from ronin_agent_patterns import Step

from .theme import ACCENT, BULLET, CONNECTOR, ERR, MUTE, OK, SOFT, TOOL, short as _short

# Models (especially open ones) love GitHub-flavored HTML line breaks — `<br>`,
# `<br/>`, `<br />` — inside table cells and paragraphs. Rich's Markdown treats
# them as raw inline HTML and prints them LITERALLY, so the rendered output is
# peppered with stray `<br>` tags. Convert them to real newlines before render.
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)


def _normalize_markdown(text: str) -> str:
    """Make LLM markdown render cleanly under rich: HTML <br> -> newline, and
    close an unterminated ``` fence so mid-stream renders stay stable (headers
    and lists stop "popping" while a code block is still open)."""
    text = _BR_RE.sub("\n", text)
    if text.count("```") % 2 == 1:
        text = text + "\n```"
    return text


def _summarize_result(result) -> str:
    """A tidy one-line preview of a tool result: item counts for file listings,
    line counts for multi-line output, else a short snippet."""
    s = str(result).strip()
    if not s:
        return ""
    # run_command output: "exit=N\n--- stdout ---\n…\n--- stderr ---\n…"
    if s.startswith("exit="):
        lines0 = s.splitlines()
        code = lines0[0][len("exit="):].strip() if lines0 else "?"
        body = ""
        if "--- stdout ---" in s:
            out = s.split("--- stdout ---", 1)[1].split("--- stderr ---")[0].strip()
            err = s.split("--- stderr ---", 1)[1].strip() if "--- stderr ---" in s else ""
            pick = (out or err).splitlines()
            body = pick[0] if pick else ""
        mark = "✓" if code == "0" else "✗"
        return f"{mark} exit {code}" + (f" · {_short(body, 68)}" if body else "")
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

    def __init__(self, console: Console, model: str = "") -> None:
        self.console = console
        self.model = model           # shown as a dim badge next to the answer bullet
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

    # On-brand thinking verbs — a different one each turn (the samurai-panda is busy).
    _THINKING = ("thinking", "forging", "sharpening", "pondering", "tracking",
                 "scheming", "reasoning", "hunting", "plotting", "weighing")

    # --- thinking spinner ----------------------------------------------------
    def start(self) -> None:
        """Begin the soft 'thinking…' animation (only on a real terminal), with a
        rotating ronin-flavoured verb."""
        if not self._is_term():
            return
        try:
            import random
            import time as _time

            from rich.text import Text
            verb = random.choice(self._THINKING)
            start_t = _time.monotonic()

            class _ThinkingLine:
                # Re-rendered every spinner frame, so the clock ticks live
                # (Claude-Code-style "forging... (12s . ctrl+c to interrupt)").
                def __rich_console__(self, console, options):
                    secs = _time.monotonic() - start_t
                    line = Text(f" {verb}... ", style=SOFT)
                    line.append(f"({secs:.0f}s · ctrl+c to interrupt)", style="dim")
                    yield line

            self._status = self.console.status(
                _ThinkingLine(), spinner="dots", spinner_style=ACCENT)
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

        from .theme import CODE_THEME
        return Markdown(_normalize_markdown(self._buf), code_theme=CODE_THEME)

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
                self.console.print(
                    f"{BULLET} [dim]{_short(self.model)}[/dim]" if self.model else BULLET)
                self._avatar_shown = True
            self.console.print(delta, end="", markup=False, highlight=False, soft_wrap=True)
            self._dirty = True
            return

        if self._live is None:
            if not self._avatar_shown:
                self.console.print(
                    f"{BULLET} [dim]{_short(self.model)}[/dim]" if self.model else BULLET)
                self._avatar_shown = True
            from rich.live import Live
            self._buf = ""
            self._live = Live(self._markdown(), console=self.console,
                              refresh_per_second=8, vertical_overflow="visible",
                              transient=False)
            self._live.start()
        self._buf += delta
        # Throttle the whole-buffer re-parse to ~10/s so a long answer does not
        # rebuild its entire Markdown on every token (O(n^2) -> smooth). The
        # final, complete render always happens in _end_text.
        import time as _t
        _now = _t.monotonic()
        if _now - getattr(self, "_last_render", 0.0) >= 0.1 or len(delta) >= 160:
            self._live.update(self._markdown())
            self._last_render = _now

    def on_reset(self) -> None:
        """A retry is about to re-stream the answer from the start (e.g. after a
        mid-stream rate-limit). Drop the partial text we have shown so the answer
        renders exactly once instead of being duplicated per retry attempt."""
        if self._live is not None:
            try:
                # Clear the partial out of the live block, then close it so the
                # retry opens a fresh one. ``transient=False`` keeps prior output,
                # so we must blank the buffer before stopping.
                self._buf = ""
                self._live.update(self._markdown())
                self._live.stop()
            except Exception:  # noqa: BLE001
                pass
            self._live = None
        self._buf = ""
        self._avatar_shown = False  # the retry re-prints the avatar before its text
        if not self._is_term():
            # Plain path: move off the unterminated inline line so the retry's
            # text starts clean (we cannot erase what was already piped).
            if self._dirty:
                self.console.print()
                self._dirty = False
        # Re-arm the thinking spinner while the retry waits out its backoff.
        self.start()

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
            # escape target/preview/etc: tool inputs and results carry arbitrary
            # text (a command like `grep "[/INST]"`, a file with [brackets], a
            # model-authored plan) that would crash Rich's markup parser otherwise.
            tgt = f"[{MUTE}]({_esc(str(target))})[/{MUTE}]" if target else ""
            self.console.print(f"{BULLET} [bold {TOOL}]{verb}[/bold {TOOL}]{tgt}")
        elif step.kind == "tool_result" and isinstance(c, dict):
            if c.get("name") == "update_todos":
                return  # the checklist was already drawn on the tool_call
            if c.get("is_error"):
                self.console.print(f"  [{ERR}]{CONNECTOR}  ✗ {_esc(_short(c.get('result', ''), 100))}[/{ERR}]")
            else:
                preview = _summarize_result(c.get("result", ""))
                if preview:
                    self.console.print(f"  [{MUTE}]{CONNECTOR}  {_esc(preview)}[/{MUTE}]")
        elif step.kind == "error":
            # error content can carry arbitrary user text (a free-text gate-denial
            # reason) with [brackets] that would crash Rich's parser.
            self.console.print(f"  [{ERR}]{CONNECTOR}  ⚠ {_esc(_short(c, 160))}[/{ERR}]")
        elif step.kind == "plan":
            self.console.print(f"  [{ACCENT}]🗂 {_esc(_short(c, 160))}[/{ACCENT}]")
        elif step.kind == "reflection":
            self.console.print(f"  [{OK}]🔎 {_esc(_short(c, 160))}[/{OK}]")

    def finish(self) -> None:
        """Call once the run is over: stop the spinner and close the text block.
        No trailing rule — turns are separated by the ``⏺`` marker + the prompt,
        which keeps the scrollback clean and Claude-Code-like (no per-turn rules)."""
        self._stop_status()
        self._end_text()
