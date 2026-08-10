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
    ApprovalRequest,
    Error,
    Event,
    Mode,
    StreamReset,
    TextDelta,
    Todo,
    ToolEnd,
    ToolResult,
    ToolStart,
    TurnEnd,
    TurnStart,
    TurnState,
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
        return replace(state, span_text="", span_thinking="", resets=state.resets + 1)
    if isinstance(event, ToolStart):
        line = ToolLine(
            tool_use_id=event.tool_use_id,
            name=event.name,
            arguments_summary=summarize_arguments(event.arguments),
        )
        return replace(state, tool_lines=(*state.tool_lines, line))
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
            todos=agent.todos if agent is not None else state.todos,
            # Spend and mode come from the state the turn actually ended with, so
            # the status line reports what ran rather than what the UI hoped ran.
            cost_usd=agent.budget.spent_usd if agent is not None else state.cost_usd,
            tokens=agent.budget.spent_tokens if agent is not None else state.tokens,
            mode=agent.mode if agent is not None else state.mode,
            cwd=agent.cwd if agent is not None else state.cwd,
        )
    return replace(state, errors=(*state.errors, event))


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


__all__ = [
    "ARGUMENT_SUMMARY_LIMIT",
    "DOUBLE_ESCAPE_WINDOW_SECONDS",
    "ELLIPSIS",
    "ERROR_PREFIX",
    "FINISHED_STATES",
    "MODE_CYCLE",
    "MODE_LABELS",
    "OK_SUMMARY",
    "PRIMARY_ARGUMENT_KEYS",
    "RESULT_ARROW",
    "RESULT_SUMMARY_LIMIT",
    "RUNNING_MARKER",
    "TOOL_MARKER",
    "EscapeAction",
    "EscapeState",
    "ToolLine",
    "ViewState",
    "mode_label",
    "next_mode",
    "press_escape",
    "reduce_all",
    "reduce_event",
    "reduce_stream",
    "summarize_arguments",
    "summarize_result",
]
