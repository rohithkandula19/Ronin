"""Event stream → view state. Pure, stdlib-only, and the module that must be right.

Every consumer of the loop (the TUI, the headless runner, an HTML export) folds the
same ``Event`` stream through :func:`reduce_event` and renders the resulting
:class:`ViewState`. The widgets are a thin skin over this file; if the fold is
wrong, every surface is wrong in the same way.

Three invariants this module exists to protect:

1. **``StreamReset`` discards only the span since the last ``TurnStart`` or
   ``StreamReset``**, never the whole transcript. That is why the text is stored as
   ``committed_text`` plus ``span_text`` rather than one string: a provider retry
   after a mid-stream drop must not render the model's answer twice, and it must
   not erase what earlier iterations of the turn already said. The provider layer
   carries regression tests for that duplication bug; so does ``tests/ui``.
2. **Nothing is buffered.** A ``TextDelta`` is appended and the resulting state is
   immediately renderable, so a caller can paint after every single delta. No
   renderer waits for ``TurnEnd``.
3. **Tool lines are derived generically.** There is no table of tool names here.
   An MCP tool nobody has heard of gets a one-line summary from the *shape* of its
   arguments and its ``ToolResult``, because the alternative — a lookup table — is
   guaranteed to be missing the tool a user actually installed.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from ronin.core.types import (
    ApprovalDecision,
    ApprovalRequest,
    Compaction,
    Error,
    Event,
    Mode,
    StreamReset,
    TextDelta,
    Todo,
    ToolEnd,
    ToolOutput,
    ToolResult,
    ToolStart,
    TurnEnd,
    TurnStart,
    TurnState,
    VerifyResult,
)

# --------------------------------------------------------------------------- #
# Rendering constants (glyphs only — colour is the renderer's business)
# --------------------------------------------------------------------------- #

#: Bullet that opens every collapsed tool line.
TOOL_MARKER = "●"
#: Separates a tool's call from its outcome.
RESULT_ARROW = "→"
#: Trails a tool line that has started but not ended.
RUNNING_MARKER = "…"
#: What a successful tool with no content to show reports.
OK_SUMMARY = "ok"
#: Prefix on the summary of a failed tool, so failure survives losing colour.
ERROR_PREFIX = "error: "

#: One-line budgets. Both cut with a trailing ellipsis; a summary must stay one line.
ARGUMENT_SUMMARY_LIMIT = 64
RESULT_SUMMARY_LIMIT = 64
ELLIPSIS = "…"

#: The spinner, as data. Frames are single-width braille so the line does not jump
#: by a column as it animates — a spinner that changes width is worse than none.
SPINNER_FRAMES: tuple[str, ...] = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

#: How often the app advances :attr:`ViewState.tick`. Fast enough to read as motion,
#: slow enough that a repaint per frame is not the reason a session feels slow.
SPINNER_INTERVAL_SECONDS = 0.1

#: How many trailing lines of a running tool's output are kept for the expansion under
#: its line. Bounded because a dev server prints forever and the pane must not grow
#: without limit; the *complete* output still reaches the model in the ``ToolResult``.
TOOL_OUTPUT_TAIL_LINES = 8

#: One line of streamed tool output, clipped. Wider than a summary — this is the
#: expansion, where a test failure's actual text is the point — but still one line.
TOOL_OUTPUT_LINE_LIMIT = 160

#: What the activity line says when a turn is live but nothing is attributable yet:
#: the model has been asked and has not started answering.
WAITING_LABEL = "waiting for the model"

#: What the activity line says while reasoning streams. Deliberately the *label* only:
#: reasoning content is shown when ``show_thinking`` is on and never merely because a
#: progress indicator needed something to display.
THINKING_LABEL = "thinking"

#: Prefixes a stream-reset notice. The user sees partial text disappear; without a reason
#: attached that reads as a bug rather than as a provider retry.
RETRY_PREFIX = "retrying — "

#: How many local (non-model) answers the notices pane keeps. Small: this is a
#: scratch area for the last thing asked, not a second transcript.
NOTICE_HISTORY = 20

#: Joins a repeated tool name to its count: ``read_file x12``. This is how the activity
#: line says "reading 12 files" without a table of tool names — see the module docstring's
#: third invariant. The tool names itself; the reducer only counts. ASCII on purpose: the
#: multiplication sign is indistinguishable from a letter in most terminal fonts.
REPEAT_GLYPH = "x"

#: Stands in for a newline inside a one-line summary, so a multi-line command
#: cannot silently reformat the tool pane.
NEWLINE_GLYPH = " ⏎ "

#: Argument *keys* (never tool names) that name the subject of a call, in priority
#: order. A tool whose arguments use none of them still renders — through the
#: ``key=value`` fallback — which is the case ``tests/ui`` pins with a made-up
#: MCP tool.
PRIMARY_ARGUMENT_KEYS: tuple[str, ...] = (
    "path",
    "file_path",
    "file",
    "command",
    "cmd",
    "pattern",
    "query",
    "url",
    "subject",
    "name",
)

#: States a turn can legally end in; a view state in one of these is finished.
FINISHED_STATES: frozenset[TurnState] = frozenset(
    {TurnState.DONE, TurnState.ERROR, TurnState.INTERRUPTED}
)


# --------------------------------------------------------------------------- #
# Generic summarizers
# --------------------------------------------------------------------------- #


def _clip(text: str, limit: int) -> str:
    """Cut to ``limit`` characters, marking the cut. Never returns a newline."""
    flat = text.replace("\r\n", "\n").replace("\n", NEWLINE_GLYPH)
    if limit <= 0 or len(flat) <= limit:
        return flat
    return flat[: max(limit - len(ELLIPSIS), 0)] + ELLIPSIS


def _scalar(value: object) -> str:
    """One-line text for one argument value, whatever the provider sent."""
    if isinstance(value, str):
        return value
    if isinstance(value, bool | int | float) or value is None:
        return str(value)
    try:
        return json.dumps(value, sort_keys=True, default=repr, ensure_ascii=False)
    except (TypeError, ValueError):  # pragma: no cover - default=repr covers this
        return repr(value)


def summarize_arguments(
    arguments: Mapping[str, Any], *, limit: int = ARGUMENT_SUMMARY_LIMIT
) -> str:
    """A one-line rendering of a tool call's arguments, derived from their shape.

    Deliberately name-blind: it prefers a conventional *argument* key, falls back
    to the single value when there is only one, and otherwise lists sorted
    ``key=value`` pairs. Sorting is what makes the line stable between runs.
    """
    if not arguments:
        return ""
    for key in PRIMARY_ARGUMENT_KEYS:
        if key in arguments:
            return _clip(_scalar(arguments[key]), limit)
    if len(arguments) == 1:
        return _clip(_scalar(next(iter(arguments.values()))), limit)
    joined = ", ".join(f"{key}={_scalar(value)}" for key, value in sorted(arguments.items()))
    return _clip(joined, limit)


def _line_count(content: str) -> int:
    if not content:
        return 0
    return content.count("\n") if content.endswith("\n") else content.count("\n") + 1


def summarize_result(result: ToolResult, *, limit: int = RESULT_SUMMARY_LIMIT) -> str:
    """A one-line outcome for a tool, derived from :class:`ToolResult` alone.

    A failure keeps a textual prefix as well as whatever colour the renderer adds,
    because a piped transcript has no colour and "the tool failed" must survive.
    """
    if not result.ok:
        return _clip(f"{ERROR_PREFIX}{result.error}", limit)
    lines = _line_count(result.content)
    if lines == 0:
        return OK_SUMMARY
    if lines > 1:
        return f"{lines} lines"
    return _clip(result.content.rstrip("\n"), limit)


# --------------------------------------------------------------------------- #
# View state
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ToolLine:
    """One tool call, collapsed to a single line.

    ``ok is None`` means the call started and has not ended — the pane shows it as
    running rather than pretending it succeeded.
    """

    tool_use_id: str
    name: str
    arguments_summary: str = ""
    result_summary: str = ""
    ok: bool | None = None
    #: The last :data:`TOOL_OUTPUT_TAIL_LINES` lines this tool has printed *so far* —
    #: the expansion under a running line, so a test suite or a dev server is visible
    #: while it runs instead of only after it exits. Empty for a tool that streams
    #: nothing, which is most of them.
    output_tail: tuple[str, ...] = ()

    @property
    def running(self) -> bool:
        return self.ok is None

    @property
    def text(self) -> str:
        head = f"{TOOL_MARKER} {self.name}({self.arguments_summary})"
        if self.ok is None:
            return f"{head} {RUNNING_MARKER}"
        return f"{head} {RESULT_ARROW} {self.result_summary}"


@dataclass(frozen=True, slots=True)
class ViewState:
    """Everything any renderer needs, folded from the event stream.

    Text is split in two on purpose. ``committed_text`` is prose from spans that
    are already settled; ``span_text`` is the current span, and it is the only
    thing ``StreamReset`` throws away. Concatenate with :attr:`text`.

    The status fields (``model``, ``cwd``, ``branch``, ``context_used``) are not
    all derivable from the stream: the loop reports spend on ``TurnEnd`` but knows
    nothing about which model name to print or how full the context window is. The
    orchestrator injects those through :meth:`with_status`, and they default to
    empty rather than to a made-up number.
    """

    turn_index: int = 0
    committed_text: str = ""
    span_text: str = ""
    committed_thinking: str = ""
    span_thinking: str = ""
    tool_lines: tuple[ToolLine, ...] = ()
    todos: tuple[Todo, ...] = ()
    pending_approval: ApprovalRequest | None = None
    errors: tuple[Error, ...] = ()
    turn_state: TurnState | None = None
    stop_reason: str = ""
    resets: int = 0
    mode: Mode = Mode.ASK
    model: str = ""
    cwd: str = "."
    branch: str = ""
    context_used: float = 0.0
    cost_usd: float = 0.0
    tokens: int = 0
    #: Animation frame for the spinner, advanced by the orchestrator's timer through
    #: :meth:`with_activity`. Kept in the state rather than in the widget so that what
    #: is on screen at frame *n* is a pure function of the state, like everything else.
    tick: int = 0
    #: How long the current in-flight work has gone without producing an event. This is
    #: the number that answers "is it stuck?", and it is supplied rather than derived:
    #: the reducer has no clock, and giving it one would make every fold untestable.
    waiting_seconds: float = 0.0
    #: Messages the user typed while a turn was running and that have not been delivered
    #: yet. They join the *running* conversation at the loop's next iteration boundary
    #: (see :mod:`ronin.core.steering`) — redirecting without killing what is in flight —
    #: but until this field existed nothing on screen said they had been received, so a
    #: correction looked lost.
    queued: tuple[str, ...] = ()
    #: Output from things that are not the model: a slash command's answer, a rewind's
    #: outcome. Kept out of the transcript on purpose — the transcript is what the model
    #: said, and folding `/help` into it would make the conversation a record of two
    #: different voices with no way to tell them apart.
    notices: tuple[str, ...] = ()

    @property
    def text(self) -> str:
        """The assistant's answer so far, reset-corrected."""
        return self.committed_text + self.span_text

    @property
    def thinking(self) -> str:
        """Reasoning so far. A renderer may hide it; the reducer still tracks it."""
        return self.committed_thinking + self.span_thinking

    @property
    def finished(self) -> bool:
        return self.turn_state in FINISHED_STATES

    @property
    def running_tools(self) -> tuple[ToolLine, ...]:
        return tuple(line for line in self.tool_lines if line.running)

    @property
    def busy(self) -> bool:
        """Whether the machine owes the user something right now.

        A turn that has started and not finished — but *not* one parked on an approval:
        then the session is waiting on the human, and animating a spinner at someone who
        is being asked a question tells them the wrong thing about who is blocked.
        """
        if self.pending_approval is not None:
            return False
        return self.turn_state is not None and not self.finished

    def with_activity(self, *, tick: int, waiting_seconds: float) -> ViewState:
        """Advance the spinner and the stuck-clock. The orchestrator owns the clock.

        The one door through which time enters the view state, so "what does the screen
        show after 3 seconds of silence" is a pure question a test answers without
        sleeping.
        """
        if waiting_seconds < 0.0:
            raise ValueError("waiting_seconds must be >= 0.0")
        return replace(self, tick=tick, waiting_seconds=waiting_seconds)

    def with_notice(self, text: str, *, keep: int = NOTICE_HISTORY) -> ViewState:
        """Record something the session said that the model did not.

        The door for slash-command output and other local answers. Bounded, because a
        session that runs `/cost` fifty times should not carry fifty answers; only the
        most recent ``keep`` survive, newest last.
        """
        if not text:
            return self
        return replace(self, notices=(*self.notices, text)[-keep:])

    def with_queued(self, text: str) -> ViewState:
        """Record a message typed mid-turn, so the user can see it landed."""
        return self if not text.strip() else replace(self, queued=(*self.queued, text))

    def with_queue(self, items: Sequence[str]) -> ViewState:
        """Replace the pending list wholesale, from whoever actually owns it.

        The append-one door above is for a view that keeps its own book. Once the
        orchestrator holds the real queue — it is the side that knows when the loop took
        a message — the screen has to be able to *follow* that list down as well as up,
        which one-at-a-time appends cannot do. Identity is preserved when nothing moved,
        because this is pulled on every event of a long turn.
        """
        pending = tuple(items)
        return self if pending == self.queued else replace(self, queued=pending)

    def cleared_queued(self) -> ViewState:
        """Drop the queue. Called when a turn starts: whatever was waiting is now running,
        and showing it as still-pending would be a lie about which turn is in flight."""
        return self if not self.queued else replace(self, queued=())

    def cleared_notices(self) -> ViewState:
        """Drop the notices. Called when a new turn starts: a command's answer belongs
        to the moment it was asked, and leaving it above a running turn reads as though
        it were part of that turn."""
        return self if not self.notices else replace(self, notices=())

    def with_tool_output(
        self,
        tool_use_id: str,
        chunk: str,
        *,
        max_lines: int = TOOL_OUTPUT_TAIL_LINES,
    ) -> ViewState:
        """Append streamed output to a running tool's expansion, keeping the tail.

        Chunks arrive at whatever boundary the pipe produced, so the existing tail and
        the new chunk are rejoined before splitting: a chunk that starts mid-line
        continues the line it belongs to instead of appearing as a new one. Only the
        last ``max_lines`` survive — the model still receives the complete output in the
        tool's ``ToolResult``; this is the human's peephole, not the record.

        An unknown or already-finished ``tool_use_id`` is a no-op rather than an error:
        output racing a ``ToolEnd`` is normal, and losing the last few lines of a
        finished tool is better than raising in a paint path.
        """
        if not chunk:
            return self
        updated: list[ToolLine] = []
        changed = False
        for line in self.tool_lines:
            if not changed and line.tool_use_id == tool_use_id and line.running:
                merged = "\n".join(line.output_tail) + chunk.replace("\r\n", "\n")
                kept = [
                    _clip(item.rstrip("\r"), TOOL_OUTPUT_LINE_LIMIT) for item in merged.split("\n")
                ]
                updated.append(replace(line, output_tail=tuple(kept[-max_lines:])))
                changed = True
            else:
                updated.append(line)
        if not changed:
            return self
        return replace(self, tool_lines=tuple(updated))

    def with_status(
        self,
        *,
        model: str | None = None,
        cwd: str | None = None,
        branch: str | None = None,
        context_used: float | None = None,
        cost_usd: float | None = None,
    ) -> ViewState:
        """Inject the status-line facts the event stream cannot supply."""
        return replace(
            self,
            model=self.model if model is None else model,
            cwd=self.cwd if cwd is None else cwd,
            branch=self.branch if branch is None else branch,
            context_used=self.context_used if context_used is None else context_used,
            cost_usd=self.cost_usd if cost_usd is None else cost_usd,
        )

    def with_mode(self, mode: Mode) -> ViewState:
        """Record the mode the user selected (see :func:`next_mode`)."""
        return replace(self, mode=mode)

    def with_todos(self, todos: Sequence[Todo]) -> ViewState:
        """Push a todo list in from a live source outside the stream.

        The stream only carries todos on ``TurnEnd`` (inside ``AgentState``), so a
        checklist that updates *during* a turn needs the orchestrator to hand the
        list over. This is that door — and it is explicit rather than the reducer
        guessing which tool name owns todos.
        """
        return replace(self, todos=tuple(todos))


def _with_tool_end(state: ViewState, event: ToolEnd) -> ViewState:
    """Close the matching started line, or record an unmatched end rather than drop it."""
    summary = summarize_result(event.result)
    updated: list[ToolLine] = []
    matched = False
    for line in state.tool_lines:
        if not matched and line.tool_use_id == event.tool_use_id and line.running:
            updated.append(replace(line, result_summary=summary, ok=event.result.ok))
            matched = True
        else:
            updated.append(line)
    if not matched:
        # A ToolEnd with no ToolStart breaks the ordering guarantee in
        # docs/ARCHITECTURE.md §4. Showing it is how the bug becomes visible.
        updated.append(
            ToolLine(
                tool_use_id=event.tool_use_id,
                name=event.name,
                result_summary=summary,
                ok=event.result.ok,
            )
        )
    pending = state.pending_approval
    if pending is not None and pending.tool_use_id == event.tool_use_id:
        pending = None
    return replace(state, tool_lines=tuple(updated), pending_approval=pending)


def reduce_event(state: ViewState, event: Event) -> ViewState:
    """Fold one event into the view state. Total, pure, and allocation-only."""
    if isinstance(event, TurnStart):
        # The span that was in flight is now settled prose: a later StreamReset
        # belongs to the *new* span and must not reach back into it.
        return replace(
            state,
            turn_index=event.turn_index,
            turn_state=event.state,
            committed_text=state.text,
            span_text="",
            committed_thinking=state.thinking,
            span_thinking="",
            stop_reason="",
        )
    if isinstance(event, TextDelta):
        if event.thinking:
            return replace(state, span_thinking=state.span_thinking + event.text)
        return replace(state, span_text=state.span_text + event.text)
    if isinstance(event, StreamReset):
        # The reason used to be discarded, which is why a retry looked like text vanishing
        # mid-answer for no reason. It is a notice rather than transcript: the model did not
        # say it, and the discarded span is already gone from the screen.
        reset = replace(state, span_text="", span_thinking="", resets=state.resets + 1)
        return reset.with_notice(f"{RETRY_PREFIX}{event.reason}") if event.reason else reset
    if isinstance(event, ToolStart):
        line = ToolLine(
            tool_use_id=event.tool_use_id,
            name=event.name,
            arguments_summary=summarize_arguments(event.arguments),
        )
        return replace(state, tool_lines=(*state.tool_lines, line))
    if isinstance(event, ToolOutput):
        return state.with_tool_output(event.tool_use_id, event.chunk)
    if isinstance(event, ToolEnd):
        return _with_tool_end(state, event)
    if isinstance(event, ApprovalRequest):
        return replace(state, pending_approval=event)
    if isinstance(event, TurnEnd):
        agent = event.agent_state
        return replace(
            state,
            turn_index=event.turn_index,
            turn_state=event.state,
            stop_reason=event.stop_reason,
            pending_approval=None,
            # `or state.todos`, not a bare read: `AgentState.todos` is empty on every
            # live turn (nothing populates it outside a resumed session), so taking it
            # unconditionally would wipe a plan pushed in mid-turn the instant the turn
            # ended. An empty list is never a deliberate clear — `parse_todos` refuses
            # one and tells the model to mark items done instead — so falling back to
            # what is already on screen cannot swallow a real "clear the list".
            todos=(agent.todos or state.todos) if agent is not None else state.todos,
            # Spend and mode come from the state the turn actually ended with, so
            # the status line reports what ran rather than what the UI hoped ran.
            cost_usd=agent.budget.spent_usd if agent is not None else state.cost_usd,
            tokens=agent.budget.spent_tokens if agent is not None else state.tokens,
            mode=agent.mode if agent is not None else state.mode,
            cwd=agent.cwd if agent is not None else state.cwd,
        )
    if isinstance(event, (Compaction, VerifyResult)):
        # Explicitly ignored, not forgotten. `ViewState` has no field for either yet,
        # and inventing one here would be UI design smuggled into a type change — the
        # renderers would then have to guess how to show a field nobody specified.
        # `ui/headless.py` already emits both losslessly as JSON, so nothing is lost
        # for scripts and tests; what is missing is a *TUI* surface, and adding one is
        # a deliberate change with a mock to review. Falling through instead would put
        # a Compaction in `state.errors`, which is typed `tuple[Error, ...]` — mypy
        # caught exactly that, which is why this branch exists.
        return state
    return replace(state, errors=(*state.errors, event))


# --------------------------------------------------------------------------- #
# What the machine is doing, in words — derived, never looked up
# --------------------------------------------------------------------------- #


def activity_label(state: ViewState) -> str:
    """One phrase for what is happening right now, or ``""`` when nothing is.

    Derived from the *shape* of what is running, never from a table of tool names —
    the same rule the tool lines follow, and for the same reason: the tool a user
    actually installed is the one missing from any table. A tool names itself, so
    twelve concurrent ``read_file`` calls render as ``read_file x12``, which reads as
    "reading 12 files" without this module ever having heard of ``read_file``.

    The order is by specificity: a running tool is the most concrete thing to report,
    reasoning next, and "waiting for the model" is the honest fallback for a live turn
    that has produced nothing yet — the moment that most looks like a hang.
    """
    if not state.busy:
        return ""
    running = state.running_tools
    if running:
        names = [line.name for line in running]
        unique = set(names)
        if len(unique) == 1 and len(names) > 1:
            return f"{names[0]} {REPEAT_GLYPH}{len(names)}"
        if len(names) == 1:
            head = names[0]
            summary = running[0].arguments_summary
            return f"{head}({summary})" if summary else head
        return ", ".join(sorted(unique)[:3]) + (f" {REPEAT_GLYPH}{len(names)}")
    if state.span_thinking:
        return THINKING_LABEL
    if state.span_text:
        # Text is already streaming onto the screen; the prose itself is the progress
        # indicator, and labelling it as well would be noise.
        return ""
    return WAITING_LABEL


def spinner_frame(tick: int, *, frames: tuple[str, ...] = SPINNER_FRAMES) -> str:
    """The frame for ``tick``. Negative ticks wrap the same way positive ones do."""
    if not frames:
        return ""
    return frames[tick % len(frames)]


def advance_activity(
    state: ViewState,
    *,
    now: float,
    last_event_at: float,
    interval: float = SPINNER_INTERVAL_SECONDS,
) -> ViewState:
    """The timer's whole job, as a pure function the app calls and nothing more.

    ``tick`` is derived from elapsed time rather than incremented, so a dropped or
    late timer callback shows the frame the clock justifies instead of falling behind.
    A clock that moves backwards yields zero rather than a negative wait.
    """
    waited = max(now - last_event_at, 0.0)
    tick = int(waited / interval) if interval > 0 else 0
    return state.with_activity(tick=tick, waiting_seconds=waited)


def reduce_all(events: Iterable[Event], state: ViewState | None = None) -> ViewState:
    """Fold a finite, already-materialized stream. Used by tests and replays."""
    current = ViewState() if state is None else state
    for event in events:
        current = reduce_event(current, event)
    return current


async def reduce_stream(
    events: AsyncIterator[Event],
    *,
    state: ViewState | None = None,
    on_state: Callable[[ViewState, Event], None] | None = None,
) -> ViewState:
    """Fold a live stream, calling ``on_state`` after **every** event.

    The callback is the anti-buffering guarantee: a consumer paints from the state
    it is handed, so the first ``TextDelta`` is on screen before the second one
    arrives.
    """
    current = ViewState() if state is None else state
    async for event in events:
        current = reduce_event(current, event)
        if on_state is not None:
            on_state(current, event)
    return current


# --------------------------------------------------------------------------- #
# Keymap logic — pure, so the TUI's two hard key behaviours are unit-testable
# --------------------------------------------------------------------------- #

#: What ``shift+tab`` walks through, as data. ``Mode.FULL`` is deliberately absent:
#: it is granted explicitly (a flag, a config) and a keystroke should not hand it out.
MODE_CYCLE: tuple[Mode, ...] = (Mode.ASK, Mode.AUTO_EDIT, Mode.PLAN)

#: What each mode is called on screen. The ladder in ``core.types`` is named for
#: permissiveness; the status line is named for what a user recognises.
MODE_LABELS: Mapping[Mode, str] = {
    Mode.PLAN: "plan",
    Mode.ASK: "normal",
    Mode.AUTO_EDIT: "auto-accept",
    Mode.FULL: "full",
}


def next_mode(mode: Mode) -> Mode:
    """The next mode in :data:`MODE_CYCLE`.

    A mode outside the cycle (``Mode.FULL``) enters at the cycle's head, which is
    its most restrictive member: cycling can only ever reduce what was granted
    out-of-band, never widen it.
    """
    if mode not in MODE_CYCLE:
        return MODE_CYCLE[0]
    return MODE_CYCLE[(MODE_CYCLE.index(mode) + 1) % len(MODE_CYCLE)]


def mode_label(mode: Mode) -> str:
    return MODE_LABELS.get(mode, mode.value)


#: How long a second ``esc`` still counts as a double-press. Named because the
#: number is the whole behaviour: too long and a deliberate second interrupt
#: rewinds the session by accident.
DOUBLE_ESCAPE_WINDOW_SECONDS = 0.6


class EscapeAction(StrEnum):
    """What an ``esc`` press means, given the press before it."""

    INTERRUPT = "interrupt"
    REWIND = "rewind"


@dataclass(frozen=True, slots=True)
class EscapeState:
    """When the last un-consumed ``esc`` landed. ``None`` means "no pending press"."""

    last_press: float | None = None


def press_escape(
    state: EscapeState,
    now: float,
    *,
    window: float = DOUBLE_ESCAPE_WINDOW_SECONDS,
) -> tuple[EscapeState, EscapeAction]:
    """Interpret one ``esc`` press: interrupt now, rewind on a quick second press.

    The first press always interrupts, immediately — waiting for the window to
    expire before stopping the model would make ``esc`` feel broken. The second
    press inside the window escalates to a rewind and consumes the pair, so a
    third press starts a fresh sequence rather than rewinding twice.

    A clock that moves backwards (a suspended laptop, a fake clock in a test)
    counts as a new sequence rather than as a press "within" the window.
    """
    previous = state.last_press
    if previous is not None and 0.0 <= now - previous <= window:
        return EscapeState(last_press=None), EscapeAction.REWIND
    return EscapeState(last_press=now), EscapeAction.INTERRUPT


# --------------------------------------------------------------------------- #
# Prompt history — pure, so walking it needs no terminal
# --------------------------------------------------------------------------- #

#: How many prompts back the input line remembers. Generous enough that a session's
#: worth of retyping is covered, bounded because this is held in memory for the life
#: of the process and an unbounded list of every prompt is a slow leak.
HISTORY_LIMIT = 200


@dataclass(frozen=True, slots=True)
class History:
    """Submitted prompts, plus where in them the input line currently is.

    ``cursor is None`` means "not browsing": the box holds live text the user is
    typing, and that text is theirs, not ours. ``draft`` is where it goes the moment
    they first press up, so that walking back and then forward returns what they had
    rather than an empty line. Losing a half-written prompt to a stray keypress is the
    thing that makes history feel unsafe to use at all, and it is the whole reason this
    type carries state instead of being a bare list plus an index.

    Frozen, and every operation hands back a new one, so browsing can never mutate
    what was submitted: recalling a prompt, editing it, and pressing up again walks
    the original entries, not the edit.
    """

    entries: tuple[str, ...] = ()
    cursor: int | None = None
    draft: str = ""

    @property
    def browsing(self) -> bool:
        return self.cursor is not None


def remember(history: History, text: str) -> History:
    """Record a submitted prompt and leave browsing.

    Blank submissions are not prompts and are dropped. A prompt identical to the most
    recent one is dropped too — holding a command down or re-sending the same thing
    twice should not make ``up`` walk through duplicates — but the same prompt *does*
    record again once something else has intervened, because then it is a real step in
    what the user did.

    Submitting always ends browsing and clears the draft: the text is on its way, so
    there is nothing left to restore.
    """
    stripped = text.strip()
    if not stripped:
        return History(entries=history.entries)
    if history.entries and history.entries[-1] == stripped:
        return History(entries=history.entries)
    entries = (*history.entries, stripped)[-HISTORY_LIMIT:]
    return History(entries=entries)


def walk_back(history: History, current: str) -> tuple[History, str | None]:
    """One step toward older prompts. ``None`` means nothing moved.

    ``current`` is what the box holds right now, and on the *first* step it is stashed
    as the draft. Later steps must not overwrite it: after up-up-down-down the user
    expects their own half-written line back, not the recalled prompt they passed
    through on the way.
    """
    if not history.entries:
        return history, None
    if history.cursor is None:
        index = len(history.entries) - 1
        return replace(history, cursor=index, draft=current), history.entries[index]
    if history.cursor == 0:
        return history, None  # already at the oldest; hold rather than wrap
    index = history.cursor - 1
    return replace(history, cursor=index), history.entries[index]


def walk_forward(history: History) -> tuple[History, str | None]:
    """One step toward newer prompts, ending on the draft. ``None`` means nothing moved.

    Stepping past the newest entry restores the draft rather than clearing the line or
    wrapping to the oldest. Wrapping would be actively hostile: the user pressed down
    to get back to what they were writing.
    """
    if history.cursor is None:
        return history, None
    if history.cursor >= len(history.entries) - 1:
        return replace(history, cursor=None, draft=""), history.draft
    index = history.cursor + 1
    return replace(history, cursor=index), history.entries[index]


# --------------------------------------------------------------------------- #
# Answering an approval — pure, so the modal has no decision of its own to make
# --------------------------------------------------------------------------- #

#: The keys that answer an :class:`~ronin.core.types.ApprovalRequest`, and what each
#: one means. Data rather than a branch in the widget, for the reason the rest of this
#: module exists: the mapping from a keystroke to "this edit may run" is the most
#: consequential decision the UI makes, and it is unit-tested with no terminal.
#:
#: ``y`` and ``a`` are the only approving keys, and neither is bound to ``enter`` or
#: ``space`` on purpose. A held-down key or a stray newline arriving while an approval
#: is on screen must not be able to approve anything — the accidental-yes is the failure
#: mode that matters here, and there is no accidental-no.
APPROVAL_KEYS: Mapping[str, bool] = {
    "y": True,
    "a": True,
    "n": False,
    "escape": False,
}

#: The key that denies *and* opens a line to say why. Absent from
#: :data:`APPROVAL_KEYS` on purpose: on its own it answers nothing, so
#: :func:`decision_for` must keep returning ``None`` for it and leave the request
#: standing until the human either supplies a reason or backs out. The widget reads
#: this name rather than the letter, for the same reason the renderer reads
#: :data:`ronin.ui.render.APPROVAL_PROMPT`: two spellings of one keymap is how they
#: drift.
REASON_KEY = "s"

#: The key that approves *and* asks for the decision to be remembered. The prompt the
#: human reads is :data:`ronin.ui.render.APPROVAL_PROMPT`, which already names these
#: three keys — one spelling of the keymap for the renderer and another for the widget
#: is how the two drift, so this module owns the meaning and that one owns the words.
REMEMBER_KEY = "a"

#: Why a denial happened, in the words the model is shown. A refusal the model cannot
#: read as deliberate reads as a malfunction, and it retries.
DENIED_BY_HUMAN = "the user declined this action"

#: What a remembered approval asks for, in the model's words.
APPROVED_AND_REMEMBERED = "approved, and remembered for the rest of this session"


def deny_with(reason: str) -> ApprovalDecision:
    """A denial carrying the human's own words, or the bare one if they gave none.

    The words travel as ``ApprovalDecision.reason``, which ``cli.approve.answer_for``
    already turns into ``Answer.feedback`` and the engine reproduces verbatim to the
    model. Nothing below this function needed building — the only thing missing was a
    way for a person to type the sentence.

    A blank reason is passed on as blank, *not* as :data:`DENIED_BY_HUMAN`. The engine
    already branches on an empty feedback and says something better than this layer
    could — "declined this action and gave no reason. Do not retry it. Propose an
    alternative" — whereas substituting a placeholder here produces "the user declined
    and said: the user declined this action", which reads as a malfunction. Saying
    nothing is what lets the layer that owns the wording own it.
    """
    return ApprovalDecision(approved=False, reason=reason.strip())


def decision_for(key: str) -> ApprovalDecision | None:
    """The decision a keypress means, or ``None`` if the key answers nothing.

    ``None`` is not a denial: an unrecognised key must leave the request standing so
    the human can still answer it. Returning a denial for every stray keystroke would
    turn a typo into a refused edit, and returning an approval for one would be
    indefensible — so the only two outcomes here are "answered" and "not an answer".
    """
    approved = APPROVAL_KEYS.get(key)
    if approved is None:
        return None
    if not approved:
        return ApprovalDecision(approved=False, reason=DENIED_BY_HUMAN)
    remember = key == REMEMBER_KEY
    return ApprovalDecision(
        approved=True,
        reason=APPROVED_AND_REMEMBERED if remember else "",
        remember=remember,
    )


__all__ = [
    "APPROVAL_KEYS",
    "APPROVED_AND_REMEMBERED",
    "ARGUMENT_SUMMARY_LIMIT",
    "DENIED_BY_HUMAN",
    "DOUBLE_ESCAPE_WINDOW_SECONDS",
    "ELLIPSIS",
    "ERROR_PREFIX",
    "FINISHED_STATES",
    "HISTORY_LIMIT",
    "MODE_CYCLE",
    "MODE_LABELS",
    "NOTICE_HISTORY",
    "OK_SUMMARY",
    "PRIMARY_ARGUMENT_KEYS",
    "REASON_KEY",
    "REMEMBER_KEY",
    "REPEAT_GLYPH",
    "RESULT_ARROW",
    "RESULT_SUMMARY_LIMIT",
    "RETRY_PREFIX",
    "RUNNING_MARKER",
    "SPINNER_FRAMES",
    "SPINNER_INTERVAL_SECONDS",
    "THINKING_LABEL",
    "TOOL_MARKER",
    "TOOL_OUTPUT_LINE_LIMIT",
    "TOOL_OUTPUT_TAIL_LINES",
    "WAITING_LABEL",
    "EscapeAction",
    "EscapeState",
    "History",
    "ToolLine",
    "ViewState",
    "activity_label",
    "advance_activity",
    "decision_for",
    "deny_with",
    "mode_label",
    "next_mode",
    "press_escape",
    "reduce_all",
    "reduce_event",
    "reduce_stream",
    "remember",
    "spinner_frame",
    "summarize_arguments",
    "summarize_result",
    "walk_back",
    "walk_forward",
]
