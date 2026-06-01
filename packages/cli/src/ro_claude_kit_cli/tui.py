"""Full-screen Textual TUI for ronin — a real coding surface.

Unlike a plain terminal REPL, the TUI has a *managed input box* separate from the
scrolling output, so you can keep typing (and queueing) while the agent works
without your keystrokes interleaving with its output. It drives the full coding
agent (``run_code_agent``): streaming replies, a live tool trace, and sensitive
actions (write / edit / run / …) gated behind an approval modal.

Launch: ``ronin`` (default) or ``ronin --tui``. Escape hatch: ``ronin --no-tui``.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, Markdown, Static

from .code_tools import SENSITIVE_TOOLS
from .config import CSKConfig, load_config

HELP_TEXT = """\
# ronin TUI

| Key | Action |
| --- | ------ |
| Enter | Send (or queue, if it's working) |
| Ctrl-L | Clear conversation + trace |
| Ctrl-Q | Quit |
| F1 | Toggle this help |

A coding agent: it reads, edits, and runs your code. Edits and shell commands
pause for your approval (y / n). **Keep typing while it works** — messages are
queued and run in order.
"""


class ApprovalScreen(ModalScreen[bool]):
    """Modal that gates a sensitive tool call. Dismisses True (approve) / False."""

    BINDINGS = [
        Binding("y", "approve", "Approve"),
        Binding("n", "deny", "Deny"),
        Binding("escape", "deny", "Deny"),
    ]

    CSS = """
    ApprovalScreen { align: center middle; }
    #box { width: 80%; max-width: 100; height: auto; border: round #e0af68;
           background: #1a1b26; padding: 1 2; }
    #title { color: #e0af68; text-style: bold; }
    #buttons { height: auto; padding-top: 1; }
    Button { margin: 0 1; }
    """

    def __init__(self, tool_name: str, args: dict) -> None:
        super().__init__()
        self._tool = tool_name
        self._args = args

    def compose(self) -> ComposeResult:
        preview = json.dumps(self._args, indent=2, default=str)[:1500]
        with Vertical(id="box"):
            yield Static(f"⚠ Approve [bold]{self._tool}[/bold]?", id="title")
            yield Markdown(f"```json\n{preview}\n```")
            with Horizontal(id="buttons"):
                yield Button("Approve (y)", variant="success", id="approve")
                yield Button("Deny (n)", variant="error", id="deny")

    @on(Button.Pressed, "#approve")
    def _approve(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#deny")
    def _deny(self) -> None:
        self.dismiss(False)

    def action_approve(self) -> None:
        self.dismiss(True)

    def action_deny(self) -> None:
        self.dismiss(False)


class RoninApp(App):
    """Textual app driving the ronin coding agent."""

    TITLE = "ronin"
    SUB_TITLE = "coding agent"

    CSS = """
    Screen { background: #16161e; }
    #main { height: 1fr; }
    #chat-pane { width: 2fr; border: round #2dd4bf; padding: 1 2; }
    #trace-pane { width: 1fr; border: round #444; padding: 1 2; }
    #trace-title, #chat-title { color: #5eead4; text-style: bold; padding-bottom: 1; }
    #input-row { height: 3; padding: 0 1; }
    Input { border: round #2dd4bf; }
    """

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", priority=True),
        Binding("ctrl+l", "clear", "Clear", priority=True),
        Binding("f1", "toggle_help", "Help"),
    ]

    def __init__(self, config: CSKConfig | None = None, root: str | Path = ".") -> None:
        super().__init__()
        self.config = config or load_config()
        self.root = str(Path(root).resolve())
        self.show_help = False
        self.busy = False
        self.history: list[tuple[str, str]] = []
        self.queue: list[str] = []
        self._live = ""              # in-progress streamed assistant text
        self._trace_lines: list[str] = []
        self.chat_md = self._initial_chat()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="main"):
            with Vertical(id="chat-pane"):
                yield Static("[bold]Conversation[/bold]", id="chat-title")
                yield VerticalScroll(Markdown(self._initial_chat()), id="chat-scroll")
            with Vertical(id="trace-pane"):
                yield Static("[bold]Trace[/bold]", id="trace-title")
                yield VerticalScroll(Markdown("_no activity yet_"), id="trace-scroll")
        with Horizontal(id="input-row"):
            yield Input(placeholder="describe a change — I'll read, edit & run code…", id="prompt")
        yield Footer()

    def _initial_chat(self) -> str:
        from . import __version__
        services = ", ".join(self.config.configured_services()) or "_(none)_"
        return (
            f"**ronin** v{__version__} · coding agent in `{self.root}`\n\n"
            f"**provider:** `{self.config.provider}` · **model:** `{self.config.resolved_model()}`"
            f" · **services:** {services}\n\n"
            "_Describe a change and press Enter. Edits & commands ask for approval._"
        )

    def on_mount(self) -> None:
        self.query_one("#prompt", Input).focus()

    # ---- input ----
    @on(Input.Submitted, "#prompt")
    def handle_submit(self, event: Input.Submitted) -> None:
        text = (event.value or "").strip()
        if not text:
            return
        self.query_one("#prompt", Input).value = ""
        if self.busy:
            self.queue.append(text)
            self._append_chat(f"**queued ›** {text}  _(runs after the current task)_")
            return
        self._start_turn(text)

    def _start_turn(self, text: str) -> None:
        self._append_chat(f"**you ›** {text}")
        self.history.append(("user", text))
        self._live = ""
        self._trace_lines = []
        self._run_agent(text)

    def _drain_queue(self) -> None:
        if self.queue and not self.busy:
            self._start_turn(self.queue.pop(0))

    # ---- agent worker (runs the full coding agent off the UI thread) ----
    def _run_agent(self, question: str) -> None:
        self.busy = True
        self._set_trace("_thinking…_")

        def worker() -> None:
            from .bg_processes import build_background_tools
            from .checkpoint import build_checkpoint_tools
            from .code_mode import run_code_agent
            from .embeddings import build_semantic_tools
            from .vision_tools import build_vision_tools

            prefix = ""
            if len(self.history) > 1:
                prior = "\n".join(f"{r.upper()}: {t}" for r, t in self.history[:-1][-6:])
                prefix = f"Conversation so far:\n{prior}"
            extra = (build_background_tools(self.root) + build_checkpoint_tools(self.root)
                     + build_vision_tools(self.config, self.root)
                     + build_semantic_tools(self.config, self.root))
            try:
                result = run_code_agent(
                    self.config, question, root=self.root, console=None, yolo=False,
                    history_prefix=prefix, extra_tools=extra, include_image_tool=False,
                    on_text_cb=self._on_text, on_step_cb=self._on_step, gate_cb=self._gate,
                )
            except Exception as exc:  # noqa: BLE001
                self.call_from_thread(self._show_error, str(exc))
                return
            self.call_from_thread(self._finish_turn, result.output, result.usage or {})

        threading.Thread(target=worker, daemon=True).start()

    # ---- bridges (called from the worker thread) ----
    def _on_text(self, delta: str) -> None:
        self.call_from_thread(self._stream_delta, delta)

    def _on_step(self, step) -> None:
        kind = getattr(step, "kind", None)
        content = getattr(step, "content", None)
        if kind == "tool_call" and isinstance(content, dict):
            args = json.dumps(content.get("input", {}), default=str)[:80]
            self.call_from_thread(self._trace_add, f"⏺ {content.get('name')}({args})")
        elif kind == "tool_result" and isinstance(content, dict):
            res = str(content.get("result", ""))[:80].replace("\n", " ")
            mark = "✗" if content.get("is_error") else "⎿"
            self.call_from_thread(self._trace_add, f"  {mark} {res}")

    def _gate(self, name: str, args: dict) -> bool:
        if name not in SENSITIVE_TOOLS:
            return True
        done = threading.Event()
        box = {"ok": False}

        def ask() -> None:
            def on_close(ok: bool | None) -> None:
                box["ok"] = bool(ok)
                done.set()
            self.push_screen(ApprovalScreen(name, args or {}), on_close)

        self.call_from_thread(ask)
        done.wait()
        return box["ok"]

    # ---- UI updates (UI thread) ----
    def _stream_delta(self, delta: str) -> None:
        self._live += delta
        self._render_live()

    def _trace_add(self, line: str) -> None:
        self._trace_lines.append(line)
        self._set_trace("```\n" + "\n".join(self._trace_lines[-40:]) + "\n```")

    def _render_live(self) -> None:
        md = self.chat_md + ("\n\n**ronin ›** " + self._live if self._live else "")
        scroll = self.query_one("#chat-scroll", VerticalScroll)
        scroll.query_one(Markdown).update(md)
        scroll.scroll_end(animate=False)

    def _finish_turn(self, output: str, usage: dict) -> None:
        final = self._live or output or "(no output)"
        self.history.append(("assistant", final))
        self._live = ""
        self._append_chat(f"**ronin ›** {final}")
        if usage:
            cached = usage.get("cache_read_input_tokens", 0)
            meta = (f"_↑{usage.get('input_tokens', 0)} ↓{usage.get('output_tokens', 0)}"
                    + (f" · ⚡{cached} cached" if cached else "") + "_")
            self._append_chat(meta)
        self.busy = False
        self._drain_queue()

    def _show_error(self, message: str) -> None:
        self._live = ""
        self._append_chat(f"**ronin ›** _error:_ `{message}`")
        self.busy = False
        self._drain_queue()

    def _append_chat(self, md_chunk: str) -> None:
        self.chat_md = (self.chat_md + "\n\n" + md_chunk).strip()
        scroll = self.query_one("#chat-scroll", VerticalScroll)
        scroll.query_one(Markdown).update(self.chat_md)
        scroll.scroll_end(animate=False)

    def _set_trace(self, md: str) -> None:
        scroll = self.query_one("#trace-scroll", VerticalScroll)
        scroll.query_one(Markdown).update(md)
        scroll.scroll_end(animate=False)

    def action_clear(self) -> None:
        self.history.clear()
        self.queue.clear()
        self.busy = False
        self._live = ""
        self._trace_lines = []
        self.chat_md = self._initial_chat()
        self.query_one("#chat-scroll", VerticalScroll).query_one(Markdown).update(self.chat_md)
        self._set_trace("_no activity yet_")

    def action_toggle_help(self) -> None:
        self.show_help = not self.show_help
        self._set_trace(HELP_TEXT if self.show_help else "_no activity yet_")


def run_tui(config: CSKConfig | None = None, root: str | Path = ".") -> None:
    RoninApp(config=config, root=root).run()
