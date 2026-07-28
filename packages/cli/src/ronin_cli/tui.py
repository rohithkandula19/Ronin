"""Full-screen Textual TUI for ronin -- a polished in-terminal coding surface.

Unlike a plain terminal REPL, the TUI has a *managed input box* separate from the
scrolling output, so you can keep typing (and queueing) while the agent works
without your keystrokes interleaving with its output. It drives the full coding
agent (``run_code_agent``): streaming markdown replies, inline work steps
(reading X / editing Y / ran tests), green/red code diffs, a live tool trace, and
sensitive actions (write / edit / run / ...) gated behind a centered approval
modal.

Layout (dark, teal-accented dashboard):

    +-----------------------------------------------------------------+
    | HEADER  ronin vX  model  cwd  *      up/down  ctx%  latency     |
    +----------+------------------------------------+-----------------+
    | SIDEBAR  |  CONVERSATION (streaming markdown) |  LIVE TRACE     |
    | model    |   steps inline, diffs colored,     |  tool calls as  |
    | free $0  |   grounded badge on answers        |  they happen    |
    | sections |                                    |                 |
    +----------+------------------------------------+-----------------+
    | > rounded input box ............................................|
    | / commands  @ files  shift+tab mode  ^c stop  /q quit   [auto]  |
    +-----------------------------------------------------------------+

Launch: ``ronin --tui``. The agent runs off the UI thread via a Textual worker
thread, so the event loop never blocks.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import Footer, Input, Markdown, Static

from .config import RoninConfig, load_config

# ---------------------------------------------------------------------------
# Palette (kept in one place so the CSS below and any inline markup agree).
# ---------------------------------------------------------------------------
BG = "#0d1117"
PANEL = "#11161d"
BORDER = "#2a3140"
TEXT = "#e6edf3"
MUTED = "#8b949e"
TEAL = "#5dcaa5"
TEAL_DEEP = "#1d9e75"
ADD_BG = "#15291c"
ADD_FG = "#73daca"
DEL_BG = "#2c161b"
DEL_FG = "#f7768e"

# Glyphs (plain unicode / box drawing, no emoji).
DOT = "●"        # status dot
RUN = "⏺"        # tool-call marker
ELBOW = "⎿"      # tool-result marker
CROSS = "✗"      # error marker
CHEV = "›"       # prompt chevron
CHECK = "✓"

HELP_TEXT = f"""\
# ronin TUI

| Key | Action |
| --- | ------ |
| Enter | Send (or queue, while it works) |
| shift+tab | Cycle mode (auto-accept / plan) |
| Ctrl+L | Clear conversation + trace |
| Ctrl+C | Stop the current turn |
| Ctrl+Q | Quit |
| F1 | Toggle this help |

A coding agent that reads, edits, and runs your code. Edits and shell commands
pause for your approval (y / n). Keep typing while it works -- messages are
queued and run in order.
"""


def _esc(text: str) -> str:
    """Escape Textual/Rich markup so arbitrary tool text can't break rendering."""
    return str(text).replace("[", r"\[")


class ApprovalScreen(ModalScreen[bool]):
    """Centered modal that gates a sensitive tool call.

    Dismisses True (approve) / False (deny). The preview shows the tool name and
    a short, human-readable summary of what it will do."""

    BINDINGS = [
        Binding("y", "approve", "Approve"),
        Binding("n", "deny", "Deny"),
        Binding("escape", "deny", "Deny"),
    ]

    DEFAULT_CSS = f"""
    ApprovalScreen {{ align: center middle; }}
    ApprovalScreen > #box {{
        width: 72; max-width: 90%; height: auto;
        border: round {TEAL}; background: {PANEL};
        padding: 1 2; color: {TEXT};
    }}
    ApprovalScreen #ap-title {{ color: {TEAL}; text-style: bold; }}
    ApprovalScreen #ap-preview {{ color: {MUTED}; padding: 1 0; }}
    ApprovalScreen #ap-capability {{ color: {DEL_FG}; }}
    ApprovalScreen #ap-hint {{ color: {MUTED}; }}
    ApprovalScreen #ap-hint .k {{ color: {TEAL}; text-style: bold; }}
    """

    def __init__(self, tool_name: str, args: dict) -> None:
        super().__init__()
        self._tool = tool_name
        self._args = args or {}

    def _preview(self) -> str:
        a = self._args
        if self._tool in ("edit_file", "edit"):
            path = a.get("path", "?")
            old = str(a.get("old_string", ""))
            new = str(a.get("new_string", ""))
            lines = [f"edit `{path}`", ""]
            for ln in old.splitlines()[:6]:
                lines.append(f"- {ln}")
            for ln in new.splitlines()[:6]:
                lines.append(f"+ {ln}")
            return "\n".join(lines)
        if self._tool in ("write_file", "write"):
            path = a.get("path", "?")
            content = str(a.get("content", ""))
            n = len(content.splitlines())
            head = "\n".join(content.splitlines()[:8])
            return f"write `{path}`  ({n} lines)\n\n{head}"
        if self._tool in ("run_command", "bash", "shell"):
            cmd = a.get("command") or a.get("cmd") or a.get("input") or a
            return f"run\n\n    {cmd}"
        # Generic: compact JSON
        return "```json\n" + json.dumps(a, indent=2, default=str)[:900] + "\n```"

    def compose(self) -> ComposeResult:
        capability_floor = self._args.get("__ronin_capability_floor")
        title = f"Approve  {_esc(self._tool)} ?"
        if isinstance(capability_floor, list) and capability_floor:
            title = f"Explicit approval required  {_esc(self._tool)}"
        with Vertical(id="box"):
            yield Static(title, id="ap-title")
            if isinstance(capability_floor, list) and capability_floor:
                yield Static(
                    "Declared high-risk capability: " + _esc(", ".join(map(str, capability_floor))),
                    id="ap-capability",
                )
            yield Markdown(self._preview(), id="ap-preview")
            yield Static(
                "[b]y[/b] approve     [b]n[/b] deny     [b]esc[/b] cancel",
                id="ap-hint",
            )

    def action_approve(self) -> None:
        self.dismiss(True)

    def action_deny(self) -> None:
        self.dismiss(False)


class RoninApp(App):
    """Textual app driving the ronin coding agent."""

    TITLE = "ronin"

    # Mode cycle for the bottom-right chip (shift+tab).
    INPUT_MODES = ("auto-accept", "plan")

    DEFAULT_CSS = f"""
    Screen {{ background: {BG}; color: {TEXT}; layers: base overlay; }}

    /* ---- top bar ---- */
    #topbar {{
        height: 1; padding: 0 1; background: {PANEL};
        color: {TEXT}; border-bottom: solid {BORDER};
    }}
    #brand {{ width: 1fr; content-align: left middle; }}
    #stats {{ width: auto; content-align: right middle; color: {MUTED}; }}

    /* ---- body ---- */
    #body {{ height: 1fr; }}

    #sidebar {{
        width: 22; min-width: 18; background: {PANEL};
        border-right: solid {BORDER}; padding: 1 1;
    }}
    #sidebar .sb-model {{ color: {TEAL}; text-style: bold; }}
    #sidebar .sb-free {{ color: {TEAL_DEEP}; }}
    #sidebar .sb-head {{ color: {MUTED}; text-style: bold; padding-top: 1; }}
    #sidebar .sb-item {{ color: {TEXT}; }}
    #sidebar .sb-dim {{ color: {MUTED}; }}

    #chat-pane {{ width: 3fr; padding: 0 1; }}
    #chat-title {{ color: {TEAL}; text-style: bold; }}
    #chat-scroll {{ background: {BG}; padding: 0 1; }}

    #trace-pane {{
        width: 30; min-width: 22; background: {PANEL};
        border-left: solid {BORDER}; padding: 0 1;
    }}
    #trace-title {{ color: {TEAL}; text-style: bold; }}
    #trace-scroll {{ background: {PANEL}; color: {MUTED}; }}

    /* diff colors inside the conversation markdown */
    .add {{ background: {ADD_BG}; color: {ADD_FG}; }}
    .del {{ background: {DEL_BG}; color: {DEL_FG}; }}

    /* ---- bottom input + footer ---- */
    #input-row {{ height: 3; padding: 0 1; background: {BG}; }}
    #prompt {{
        border: round {TEAL}; background: {PANEL}; color: {TEXT};
        padding: 0 1;
    }}
    #prompt:focus {{ border: round {TEAL}; }}
    #hintbar {{ height: 1; padding: 0 2; background: {BG}; color: {MUTED}; }}
    #hints {{ width: 1fr; content-align: left middle; }}
    #mode-chip {{ width: auto; content-align: right middle;
                 color: {TEAL}; text-style: bold; }}
    """

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", priority=True),
        Binding("ctrl+l", "clear", "Clear", priority=True),
        Binding("ctrl+c", "stop", "Stop", priority=True),
        Binding("shift+tab", "cycle_mode", "Mode"),
        Binding("f1", "toggle_help", "Help"),
    ]

    # reactive stats shown in the top-right of the header
    tokens_up: reactive[int] = reactive(0)
    tokens_down: reactive[int] = reactive(0)
    context_pct: reactive[int] = reactive(0)
    last_latency: reactive[float] = reactive(0.0)
    mode_index: reactive[int] = reactive(0)

    def __init__(self, config: RoninConfig | None = None, root: str | Path = ".") -> None:
        super().__init__()
        self.config = config or load_config()
        self.root = str(Path(root).resolve())
        self.show_help = False
        self.busy = False
        self.history: list[tuple[str, str]] = []
        self.queue: list[str] = []
        self._live = ""              # in-progress streamed assistant text
        self._trace_lines: list[str] = []
        self._turn_start = 0.0
        self._stop_flag = False
        self.chat_md = self._initial_chat()

    # ---- header / stats ----
    def _model_label(self) -> str:
        return f"{self.config.provider} {DOT} {self.config.resolved_model()}"

    def _cwd_label(self) -> str:
        home = str(Path.home())
        p = self.root
        if p.startswith(home):
            p = "~" + p[len(home):]
        # keep it short
        return p if len(p) <= 40 else "..." + p[-37:]

    def _stats_label(self) -> str:
        lat = f"{self.last_latency:.1f}s" if self.last_latency else "--"
        return (f"↑{self.tokens_up} ↓{self.tokens_down}   "
                f"ctx {self.context_pct}%   {lat}")

    def _refresh_topbar(self) -> None:
        try:
            from . import __version__
            ver = __version__
        except Exception:  # noqa: BLE001
            ver = "?"
        brand = (f"[b {TEAL}]{DOT}[/] [b]ronin[/b] [dim]v{ver}[/dim]   "
                 f"[{TEAL}]{_esc(self._model_label())}[/]   "
                 f"[dim]{_esc(self._cwd_label())}[/dim]")
        try:
            self.query_one("#brand", Static).update(brand)
            self.query_one("#stats", Static).update(f"[dim]{self._stats_label()}[/dim]")
        except Exception:  # noqa: BLE001 -- not mounted yet
            pass

    def watch_tokens_up(self) -> None: self._refresh_topbar()
    def watch_tokens_down(self) -> None: self._refresh_topbar()
    def watch_context_pct(self) -> None: self._refresh_topbar()
    def watch_last_latency(self) -> None: self._refresh_topbar()

    def watch_mode_index(self) -> None:
        try:
            self.query_one("#mode-chip", Static).update(
                f"[{TEAL}]◈ {self.INPUT_MODES[self.mode_index % len(self.INPUT_MODES)]}[/]")
        except Exception:  # noqa: BLE001
            pass

    # ---- compose ----
    def compose(self) -> ComposeResult:
        with Horizontal(id="topbar"):
            yield Static("", id="brand")
            yield Static("", id="stats")
        with Horizontal(id="body"):
            with Vertical(id="sidebar"):
                yield from self._sidebar_widgets()
            with Vertical(id="chat-pane"):
                yield Static("Conversation", id="chat-title")
                yield VerticalScroll(Markdown(self.chat_md), id="chat-scroll")
            with Vertical(id="trace-pane"):
                yield Static("Live trace", id="trace-title")
                yield VerticalScroll(Static("[dim]no activity yet[/dim]"), id="trace-scroll")
        with Horizontal(id="input-row"):
            yield Input(
                placeholder="describe a change -- I'll read, edit & run code",
                id="prompt",
            )
        with Horizontal(id="hintbar"):
            yield Static(
                "[dim]/ commands  ·  @ files  ·  shift+tab mode  "
                "·  ^c stop  ·  /q quit[/dim]",
                id="hints",
            )
            yield Static("", id="mode-chip")
        yield Footer()

    def _sidebar_widgets(self):
        yield Static(_esc(self.config.resolved_model()), classes="sb-model")
        yield Static("free · local · $0", classes="sb-free")
        yield Static("Sections", classes="sb-head")
        for name in ("Chat", "Runs", "Memory"):
            yield Static(f"  {name}", classes="sb-item")
        yield Static("Recent runs", classes="sb-head")
        yield Static("", id="sb-runs", classes="sb-dim")
        yield Static("Memory", classes="sb-head")
        yield Static("", id="sb-memory", classes="sb-dim")

    def _load_sidebar_data(self) -> None:
        """Wire the sidebar to real run_store / memory data (cheap, best-effort)."""
        runs_text = "[dim]none yet[/dim]"
        try:
            from .run_store import list_runs
            runs = list_runs(limit=5)
            if runs:
                rows = []
                for r in runs:
                    mark = CHECK if r.get("success") else CROSS
                    goal = (r.get("goal") or r.get("kind") or "run")[:16]
                    rows.append(f"  {mark} {_esc(goal)}")
                runs_text = "\n".join(rows)
        except Exception:  # noqa: BLE001
            pass
        mem_text = "[dim]none yet[/dim]"
        try:
            from .memory_store import list_memories
            mems = list_memories()
            if mems:
                mem_text = "\n".join(f"  · {_esc(m[:16])}" for m in mems[:4])
        except Exception:  # noqa: BLE001
            pass
        try:
            self.query_one("#sb-runs", Static).update(runs_text)
            self.query_one("#sb-memory", Static).update(mem_text)
        except Exception:  # noqa: BLE001
            pass

    def _initial_chat(self) -> str:
        from . import __version__
        services = ", ".join(self.config.configured_services()) or "_(none)_"
        return (
            f"### ronin v{__version__}\n\n"
            f"A coding agent in `{self.root}`.\n\n"
            f"**model** `{self.config.resolved_model()}` · "
            f"**provider** `{self.config.provider}` · "
            f"**services** {services}\n\n"
            "Describe a change and press Enter. Edits and commands ask for "
            "approval. Keep typing while it works."
        )

    def on_mount(self) -> None:
        self.query_one("#prompt", Input).focus()
        self._refresh_topbar()
        self.watch_mode_index()
        self._load_sidebar_data()

    # ---- input ----
    @on(Input.Submitted, "#prompt")
    def handle_submit(self, event: Input.Submitted) -> None:
        text = (event.value or "").strip()
        self.query_one("#prompt", Input).value = ""
        if not text:
            return
        # slash quit shortcuts
        if text in ("/q", "/quit", "/exit"):
            self.exit()
            return
        if text in ("/clear", "/reset"):
            self.action_clear()
            return
        if self.busy:
            self.queue.append(text)
            self._append_chat(f"**queued {CHEV}** {_esc(text)}  _(runs after the current task)_")
            return
        self._start_turn(text)

    def _start_turn(self, text: str) -> None:
        self._append_chat(f"**you {CHEV}** {_esc(text)}")
        self.history.append(("user", text))
        self._live = ""
        self._trace_lines = []
        self._stop_flag = False
        self._turn_start = time.monotonic()
        self._run_agent(text)

    def _drain_queue(self) -> None:
        if self.queue and not self.busy:
            self._start_turn(self.queue.pop(0))

    # ---- agent worker (runs the full coding agent off the UI thread) ----
    def _run_agent(self, question: str) -> None:
        self.busy = True
        self._set_trace(f"[{TEAL}]thinking...[/]")

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
            read_only = self.INPUT_MODES[self.mode_index % len(self.INPUT_MODES)] == "plan"
            try:
                result = run_code_agent(
                    self.config, question, root=self.root, console=None, yolo=False,
                    history_prefix=prefix, extra_tools=extra, include_image_tool=False,
                    read_only=read_only,
                    on_text_cb=self._on_text, on_reset_cb=self._on_reset,
                    on_step_cb=self._on_step, gate_cb=self._gate,
                )
            except Exception as exc:  # noqa: BLE001
                self.call_from_thread(self._show_error, str(exc))
                return
            self.call_from_thread(self._finish_turn, result.output, result.usage or {},
                                  getattr(result, "faithfulness", None))

        self.run_worker(worker, thread=True, exclusive=False, name="agent")

    # ---- bridges (called from the worker thread) ----
    def _on_text(self, delta: str) -> None:
        self.call_from_thread(self._stream_delta, delta)

    def _on_reset(self) -> None:
        # A retry re-streams the answer from the start (e.g. after a mid-stream
        # rate-limit); drop the partial so it isn't shown twice.
        self.call_from_thread(self._stream_reset)

    def _on_step(self, step) -> None:
        kind = getattr(step, "kind", None)
        content = getattr(step, "content", None)
        if kind == "tool_call" and isinstance(content, dict):
            name = str(content.get("name", "tool"))
            args = content.get("input", {}) or {}
            self.call_from_thread(self._trace_tool, name, args)
            # human-readable inline step in the conversation
            label = self._inline_step_label(name, args)
            if label:
                self.call_from_thread(self._append_step, label)
            diff = self._diff_block(name, args)
            if diff:
                self.call_from_thread(self._append_chat, diff)
        elif kind == "tool_result" and isinstance(content, dict):
            res = str(content.get("result", ""))[:90].replace("\n", " ")
            err = bool(content.get("is_error"))
            self.call_from_thread(self._trace_result, res, err)

    @staticmethod
    def _inline_step_label(name: str, args: dict) -> str | None:
        path = args.get("path")
        if name in ("read_file", "read") and path:
            return f"reading `{path}`"
        if name in ("edit_file", "edit", "multi_edit") and path:
            return f"editing `{path}`"
        if name in ("write_file", "write") and path:
            return f"writing `{path}`"
        if name in ("run_command", "bash", "shell"):
            cmd = str(args.get("command") or args.get("cmd") or "")[:60]
            return f"ran `{_esc(cmd)}`"
        if name in ("search_files", "grep", "glob"):
            q = str(args.get("query") or args.get("pattern") or "")[:40]
            return f"searching `{_esc(q)}`"
        return None

    @staticmethod
    def _diff_block(name: str, args: dict) -> str | None:
        """Render an edit as a fenced diff so the markdown shows +/- lines.

        The chat Markdown widget styles ``diff`` fences with add/del colors via
        Syntax highlighting, giving green/red line backgrounds for free."""
        if name not in ("edit_file", "edit"):
            return None
        old = str(args.get("old_string", ""))
        new = str(args.get("new_string", ""))
        if not old and not new:
            return None
        lines = ["```diff"]
        for ln in old.splitlines()[:12]:
            lines.append(f"-{ln}")
        for ln in new.splitlines()[:12]:
            lines.append(f"+{ln}")
        lines.append("```")
        return "\n".join(lines)

    def _gate(self, name: str, args: dict) -> bool:
        # run_code_agent only routes tools that actually need approval here
        # (built-in mutators + sensitive MCP/plugin tools), so always prompt.
        if self._stop_flag:
            return False
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

    def _stream_reset(self) -> None:
        self._live = ""
        self._render_live()

    def _trace_tool(self, name: str, args: dict) -> None:
        preview = json.dumps(args, default=str)[:46]
        self._trace_lines.append(f"[{TEAL}]{RUN}[/] {_esc(name)}[dim]({_esc(preview)})[/dim]")
        self._flush_trace()

    def _trace_result(self, res: str, err: bool) -> None:
        mark = f"[{DEL_FG}]{CROSS}[/]" if err else f"[dim]{ELBOW}[/]"
        self._trace_lines.append(f"  {mark} [dim]{_esc(res)}[/dim]")
        self._flush_trace()

    def _flush_trace(self) -> None:
        body = "\n".join(self._trace_lines[-60:]) or "[dim]no activity yet[/dim]"
        self._set_trace(body)

    def _render_live(self) -> None:
        md = self.chat_md + ("\n\n**ronin " + CHEV + "** " + self._live if self._live else "")
        scroll = self.query_one("#chat-scroll", VerticalScroll)
        scroll.query_one(Markdown).update(md)
        scroll.scroll_end(animate=False)

    def _append_step(self, label: str) -> None:
        self._append_chat(f"[dim]{RUN}[/dim] {label}")

    def _finish_turn(self, output: str, usage: dict, faithfulness=None) -> None:
        final = self._live or output or "(no output)"
        self.history.append(("assistant", final))
        self._live = ""
        badge = ""
        score = None
        try:
            score = getattr(faithfulness, "score", None)
            if score is None and isinstance(faithfulness, dict):
                score = faithfulness.get("score")
        except Exception:  # noqa: BLE001
            score = None
        if isinstance(score, (int, float)):
            badge = f"  `grounded {score:.2f}`"
        self._append_chat(f"**ronin {CHEV}** {final}{badge}")
        if usage:
            up = usage.get("input_tokens", 0)
            down = usage.get("output_tokens", 0)
            cached = usage.get("cache_read_input_tokens", 0)
            self.tokens_up += up
            self.tokens_down += down
            # rough context fill: a 128k window, capped at 100
            self.context_pct = min(100, int((up + down) * 100 / 128000))
            meta = (f"_↑{up} ↓{down}"
                    + (f" · ⚡{cached} cached" if cached else "") + "_")
            self._append_chat(meta)
        if self._turn_start:
            self.last_latency = time.monotonic() - self._turn_start
        self.busy = False
        self._drain_queue()

    def _show_error(self, message: str) -> None:
        self._live = ""
        self._append_chat(f"**ronin {CHEV}** _error:_ `{_esc(message)}`")
        self.busy = False
        self._drain_queue()

    def _append_chat(self, md_chunk: str) -> None:
        self.chat_md = (self.chat_md + "\n\n" + md_chunk).strip()
        scroll = self.query_one("#chat-scroll", VerticalScroll)
        scroll.query_one(Markdown).update(self.chat_md)
        scroll.scroll_end(animate=False)

    def _set_trace(self, markup: str) -> None:
        scroll = self.query_one("#trace-scroll", VerticalScroll)
        scroll.query_one(Static).update(markup)
        scroll.scroll_end(animate=False)

    # ---- actions ----
    def action_clear(self) -> None:
        self.history.clear()
        self.queue.clear()
        self.busy = False
        self._live = ""
        self._trace_lines = []
        self.chat_md = self._initial_chat()
        self.query_one("#chat-scroll", VerticalScroll).query_one(Markdown).update(self.chat_md)
        self._set_trace("[dim]no activity yet[/dim]")

    def action_stop(self) -> None:
        """Ctrl+C: stop the current turn. If idle, this is a no-op (quit is ^q)."""
        if self.busy:
            self._stop_flag = True
            self._append_chat("[dim]stopping...[/dim]")
        # do not exit the app -- ^q quits.

    def action_cycle_mode(self) -> None:
        self.mode_index = (self.mode_index + 1) % len(self.INPUT_MODES)

    def action_toggle_help(self) -> None:
        self.show_help = not self.show_help
        if self.show_help:
            self._set_trace(HELP_TEXT.replace("# ", "").replace("|", " "))
        else:
            self._flush_trace()


def run_tui(config: RoninConfig | None = None, root: str | Path = ".") -> None:
    RoninApp(config=config, root=root).run()
