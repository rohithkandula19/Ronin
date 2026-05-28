"""Full-screen Textual TUI for ronin.

Launch with: ``ronin tui``

Layout:
- Header with provider/model/services
- Left pane: conversation history (markdown-friendly)
- Right pane: live trace of the current/last run
- Bottom: input box
- Footer with keybindings (Ctrl-Q quit, Ctrl-L clear, F1 help)

Memory persists across turns within a session via ShortTermMemory.
"""
from __future__ import annotations

import json
import threading
from typing import Any

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Footer, Header, Input, Markdown, Static

from .config import CSKConfig, load_config
from .runner import AgentResultRich, run_ask


HELP_TEXT = """\
# ronin TUI

| Key | Action |
| --- | ------ |
| Enter | Send your message |
| Ctrl-L | Clear conversation + trace |
| Ctrl-Q | Quit |
| F1 | Toggle this help |
| Up / Down | (focus the input) edit your draft |

Type a request and press Enter. The right pane shows the agent's trace
in real time. Conversation memory persists for the lifetime of the session.

**You can keep typing while it works** — messages you send mid-task are
queued and run in order as soon as the current one finishes.
"""


class RoninApp(App):
    """Textual app wrapping the ronin agent."""

    TITLE = "ronin"
    SUB_TITLE = "one assistant for everything"

    CSS = """
    Screen { background: #16161e; }

    #main { height: 1fr; }

    #chat-pane {
        width: 2fr;
        border: round #2dd4bf;
        padding: 1 2;
    }

    #trace-pane {
        width: 1fr;
        border: round #444;
        padding: 1 2;
    }

    #trace-title, #chat-title { color: #5eead4; text-style: bold; padding-bottom: 1; }

    #input-row {
        height: 3;
        padding: 0 1;
    }

    Input { border: round #2dd4bf; }

    .help { background: #222; padding: 1 2; }
    """

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", priority=True),
        Binding("ctrl+l", "clear", "Clear", priority=True),
        Binding("f1", "toggle_help", "Help"),
    ]

    def __init__(self, config: CSKConfig | None = None) -> None:
        super().__init__()
        self.config = config or load_config()
        self.show_help = False
        self.busy = False
        self.history: list[tuple[str, str]] = []  # (role, text) — for memory across turns
        self.queue: list[str] = []  # messages typed while the agent is busy
        self.chat_md = self._initial_chat()  # we own the chat markdown (no private API)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="main"):
            with Vertical(id="chat-pane"):
                yield Static("[bold]Conversation[/bold]", id="chat-title")
                yield VerticalScroll(Markdown(self._initial_chat()), id="chat-scroll")
            with Vertical(id="trace-pane"):
                yield Static("[bold]Trace[/bold]", id="trace-title")
                yield VerticalScroll(Markdown("_no trace yet — ask a question_"), id="trace-scroll")
        with Horizontal(id="input-row"):
            yield Input(placeholder="ask me anything — your data, a question, anything…", id="prompt")
        yield Footer()

    def _initial_chat(self) -> str:
        import random
        from . import __version__
        from .panda_art import PANDA_ACTIVITIES, normalize_frames

        # Pick a random activity and embed its first frame as a monospace block.
        # Textual's Markdown widget renders fenced code blocks in monospace,
        # which keeps the kaomoji art aligned.
        activity = random.choice(list(PANDA_ACTIVITIES))
        frame = normalize_frames(PANDA_ACTIVITIES[activity])[0]
        panda = "```\n" + "\n".join(frame) + "\n```"
        services = ", ".join(self.config.configured_services()) or "_(none)_"
        return (
            f"{panda}\n\n"
            f"**ronin** v{__version__} · masterless Claude agent · _{activity}_\n\n"
            f"**provider:** `{self.config.provider}` · "
            f"**model:** `{self.config.resolved_model()}` · "
            f"**services:** {services}\n\n"
            f"_Type a question below and press Enter._  "
            f"_For code editing with approval gating, exit and run_ "
            f"`ronin code` _or_ `ronin --no-tui`."
        )

    def on_mount(self) -> None:
        self.query_one("#prompt", Input).focus()

    @on(Input.Submitted, "#prompt")
    def handle_submit(self, event: Input.Submitted) -> None:
        text = (event.value or "").strip()
        if not text:
            return
        self.query_one("#prompt", Input).value = ""
        if self.busy:
            # type-ahead: queue it and run it when the current turn finishes
            self.queue.append(text)
            self._append_chat(f"**queued ›** {text}  _(runs after the current task)_")
            return
        self._start_turn(text)

    def _start_turn(self, text: str) -> None:
        self._append_chat(f"**you ›** {text}")
        self.history.append(("user", text))
        self._run_agent(text)

    def _drain_queue(self) -> None:
        """After a turn finishes, run the next queued message (if any)."""
        if self.queue and not self.busy:
            self._start_turn(self.queue.pop(0))

    def _run_agent(self, question: str) -> None:
        """Run the agent in a thread; updates UI on completion."""
        self.busy = True
        self._set_trace("_thinking…_")

        def worker() -> None:
            try:
                # Stitch a tiny conversation prefix so multi-turn works.
                if len(self.history) > 1:
                    prior = "\n\n".join(f"{r.upper()}: {t}" for r, t in self.history[:-1])
                    full = f"Conversation so far:\n{prior}\n\nUser's new message: {question}"
                else:
                    full = question
                result = run_ask(self.config, full, console=None)
            except Exception as exc:  # noqa: BLE001
                self.call_from_thread(self._show_error, str(exc))
                return
            self.call_from_thread(self._show_result, result)

        threading.Thread(target=worker, daemon=True).start()

    def _show_result(self, result: AgentResultRich) -> None:
        self.history.append(("assistant", result.output))
        self._append_chat(f"**ronin ›** {result.output}")
        self._set_trace(self._render_trace(result))
        meta = f"_iterations: {result.iterations}_"
        if result.usage:
            meta += f" · _in: {result.usage.get('input_tokens', 0)} / out: {result.usage.get('output_tokens', 0)}_"
        if result.demo_mode:
            meta += " · _demo mode_"
        self._append_chat(meta)
        self.busy = False
        self._drain_queue()

    def _show_error(self, message: str) -> None:
        self._append_chat(f"**ronin ›** _error:_ `{message}`")
        self.busy = False
        self._drain_queue()

    def _render_trace(self, result: AgentResultRich) -> str:
        if not result.trace:
            return "_no steps_"
        lines: list[str] = []
        for i, step in enumerate(result.trace, 1):
            kind = step.get("kind", "?")
            content = step.get("content")
            if not isinstance(content, str):
                content = json.dumps(content, indent=2, default=str)
            lines.append(f"**{i}. {kind}**")
            lines.append("```\n" + content[:1000] + "\n```")
        return "\n\n".join(lines)

    def _append_chat(self, md_chunk: str) -> None:
        self.chat_md = (self.chat_md + "\n\n" + md_chunk).strip()
        scroll = self.query_one("#chat-scroll", VerticalScroll)
        scroll.query_one(Markdown).update(self.chat_md)
        scroll.scroll_end(animate=False)

    def _set_trace(self, md: str) -> None:
        scroll = self.query_one("#trace-scroll", VerticalScroll)
        scroll.query_one(Markdown).update(md)
        scroll.scroll_home(animate=False)

    def action_clear(self) -> None:
        self.history.clear()
        self.queue.clear()
        self.busy = False
        self.chat_md = self._initial_chat()
        self.query_one("#chat-scroll", VerticalScroll).query_one(Markdown).update(self.chat_md)
        self._set_trace("_no trace yet — ask a question_")

    def action_toggle_help(self) -> None:
        self.show_help = not self.show_help
        if self.show_help:
            self._set_trace(HELP_TEXT)
        else:
            self._set_trace("_no trace yet — ask a question_")


def run_tui(config: CSKConfig | None = None) -> None:
    RoninApp(config=config).run()
