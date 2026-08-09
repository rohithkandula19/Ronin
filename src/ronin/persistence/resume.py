"""Replay: fold a recorded event stream back into a resumable ``AgentState``.

Three things make this more than a loop over events.

**1. ``StreamReset`` must be honoured.** It invalidates the ``TextDelta`` events
emitted since the last ``TurnStart`` or ``StreamReset``, whichever is later, and
nothing else — a tool that already ran cannot be un-run. Getting this wrong
concatenates the pre-retry and post-retry text and the assistant appears to answer
twice; the provider layer already carries regression tests for exactly that
duplication, and a replay that reintroduces it would be the same bug wearing a
different hat. :func:`fold_turns` is the one implementation of that rule, and
:mod:`ronin.persistence.export` uses it too so the exported transcript and the
resumed state can never disagree about what the model said.

**2. ``TurnEnd.agent_state`` is preferred, reconciled — and then built on.** The
recorded state is strictly richer than anything folding can produce: it carries
``Thinking.signature`` (which no ``TextDelta`` records and which must survive
byte-identical), the ``Budget``, the mode, the cwd, the todos, and any system
message the loop injected. So the newest one wins as the base. But "wins" without a
check is how a codec bug ships silently, so the turns up to that checkpoint are
compared against it on the parts both can know — the tool-use ids, the tool-result
ids with their error flags, and the assistant prose — and any disagreement lands in
:attr:`ReplayResult.mismatches` for the caller to show.

The comparison is a *suffix* match, not equality: a session that was itself resumed
carries history in its state that the events of this recording never contained, and
demanding equality there would report a mismatch on every continued session and
train the reader to ignore the list. A suffix match still catches the failure that
matters — duplicated or reordered prose and calls.

Turns recorded *after* the last checkpoint are folded on top of it. A crash happens
mid-turn by definition, so the newest ``TurnEnd`` is usually one or more turns
behind the end of the file; taking the checkpoint alone would silently throw away
the tool call the session died in the middle of.

**3. An unanswered tool call is repaired, not rejected.** A crash between
``ToolStart`` and the turn's end is the *normal* shape of an interrupted session,
and a transcript with an unpaired ``tool_use`` id is one every provider rejects. So
replay answers it: with the recorded ``ToolEnd`` result if the crash landed after
the tool finished, otherwise with an explicit :data:`INTERRUPTED_NO_RESULT` error
block. The invariant is asserted afterwards — ``state.pairing_errors`` is empty or
:class:`ReplayError` is raised — because a state that cannot be sent is not a
resume, it is a delayed crash.

``ronin --resume`` picks a session from :func:`sessions_for_cwd`; ``ronin
--continue`` is :func:`resume_latest`, and it filters on cwd because "the most
recent session" almost never means the one from another checkout.
"""
from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path

from ..core.types import (
    AgentState,
    ApprovalRequest,
    ContentBlock,
    Error,
    Event,
    Message,
    Role,
    StreamReset,
    Text,
    TextDelta,
    Thinking,
    ToolEnd,
    ToolResultBlock,
    ToolStart,
    ToolUse,
    TurnEnd,
    TurnStart,
    unpaired_tool_uses,
)
from .transcript import (
    ReadResult,
    SessionMeta,
    list_sessions,
    read_events,
    session_path,
)

#: What a tool call that never came back is answered with. Phrased for the model:
#: it has to be able to tell "this failed" from "this never ran".
INTERRUPTED_NO_RESULT = (
    "interrupted: the session ended before this tool returned, so there is no "
    "result. Re-run it if you still need the answer."
)


class ReplayError(RuntimeError):
    """A recorded stream could not be folded into a sendable state."""


class StateSource(StrEnum):
    """Where the replayed state came from, so a caller can say how solid it is."""

    #: A ``TurnEnd.agent_state`` the loop recorded. Complete, signatures included.
    RECORDED = "recorded"
    #: Folded from events alone. Messages only — no budget, no thinking signatures.
    FOLDED = "folded"


@dataclass(frozen=True, slots=True)
class ReplayTurn:
    """One turn as the recording saw it, with ``StreamReset`` already applied."""

    index: int
    text: str = ""
    thinking: str = ""
    resets: int = 0
    calls: tuple[ToolUse, ...] = ()
    results: tuple[ToolResultBlock, ...] = ()
    approvals: tuple[ApprovalRequest, ...] = ()
    errors: tuple[Error, ...] = ()
    end: TurnEnd | None = None

    @property
    def unanswered(self) -> tuple[str, ...]:
        answered = {block.tool_use_id for block in self.results}
        return tuple(call.id for call in self.calls if call.id not in answered)


@dataclass(slots=True)
class _TurnBuilder:
    """Accumulator for one turn. Mutable because a fold is accumulation."""

    index: int
    #: True when no ``TurnStart`` opened this turn, so its index is a guess until a
    #: ``TurnEnd`` tells us the real one.
    implicit: bool = False
    text: list[str] = field(default_factory=list)
    thinking: list[str] = field(default_factory=list)
    resets: int = 0
    calls: list[ToolUse] = field(default_factory=list)
    results: list[ToolResultBlock] = field(default_factory=list)
    approvals: list[ApprovalRequest] = field(default_factory=list)
    errors: list[Error] = field(default_factory=list)
    end: TurnEnd | None = None

    def reset_text(self) -> None:
        """The whole of ``StreamReset``: prose and reasoning, tools untouched."""
        self.text.clear()
        self.thinking.clear()

    def finish(self) -> ReplayTurn:
        order = {call.id: position for position, call in enumerate(self.calls)}
        results = sorted(
            self.results, key=lambda block: order.get(block.tool_use_id, len(order))
        )
        return ReplayTurn(
            index=self.index,
            text="".join(self.text),
            thinking="".join(self.thinking),
            resets=self.resets,
            calls=tuple(self.calls),
            results=tuple(results),
            approvals=tuple(self.approvals),
            errors=tuple(self.errors),
            end=self.end,
        )


def fold_turns(events: Sequence[Event]) -> tuple[ReplayTurn, ...]:
    """Split a recorded stream into turns, honouring ``StreamReset``.

    A turn opens on ``TurnStart`` and closes on ``TurnEnd`` or the next
    ``TurnStart``; a stream that begins mid-turn (a transcript whose head was
    trimmed) opens an implicit turn rather than dropping the events. Turn *indices*
    repeat across a session — every ``run_turn`` call starts at 0 — so position in
    this tuple, not ``index``, is the identity of a turn.
    """
    turns: list[ReplayTurn] = []
    current: _TurnBuilder | None = None

    for event in events:
        if isinstance(event, TurnStart):
            if current is not None:
                turns.append(current.finish())
            current = _TurnBuilder(index=event.turn_index)
            continue
        if current is None:
            current = _TurnBuilder(index=0, implicit=True)
        if isinstance(event, TextDelta):
            (current.thinking if event.thinking else current.text).append(event.text)
        elif isinstance(event, StreamReset):
            current.resets += 1
            current.reset_text()
        elif isinstance(event, ToolStart):
            current.calls.append(
                ToolUse(id=event.tool_use_id, name=event.name, arguments=event.arguments)
            )
        elif isinstance(event, ToolEnd):
            current.results.append(event.result.as_block(event.tool_use_id))
        elif isinstance(event, ApprovalRequest):
            current.approvals.append(event)
        elif isinstance(event, Error):
            current.errors.append(event)
        elif isinstance(event, TurnEnd):
            current.end = event
            if current.implicit:
                current.index = event.turn_index
            turns.append(current.finish())
            current = None
    if current is not None:
        turns.append(current.finish())
    return tuple(turns)


@dataclass(frozen=True, slots=True)
class ReplayResult:
    """The folded session: the state to resume from, and every caveat about it.

    The brief's signature is ``replay(events) -> AgentState``; ``.state`` is that
    value. The rest of this record exists because "replayed cleanly" and "replayed
    with three unanswered tool calls and a text mismatch" must not look the same to
    the caller that is about to continue the conversation.
    """

    state: AgentState
    source: StateSource
    turns: tuple[ReplayTurn, ...] = ()
    mismatches: tuple[str, ...] = ()
    repairs: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def clean(self) -> bool:
        return not self.mismatches and not self.repairs


def _folded_messages(turns: Sequence[ReplayTurn]) -> tuple[Message, ...]:
    """The transcript the events alone imply: assistant message, then results.

    Mirrors what ``core.loop`` appends per iteration — one assistant message
    carrying the prose and every ``tool_use``, then one tool-role message answering
    the whole batch, which is what keeps pairing trivially satisfied.
    """
    messages: list[Message] = []
    for turn in turns:
        blocks: list[ContentBlock] = []
        if turn.thinking:
            # signature is unrecoverable from TextDelta; see the module docstring.
            blocks.append(Thinking(text=turn.thinking))
        if turn.text:
            blocks.append(Text(text=turn.text))
        blocks.extend(turn.calls)
        if blocks:
            messages.append(Message(role=Role.ASSISTANT, content_blocks=tuple(blocks)))
        if turn.results:
            messages.append(Message(role=Role.TOOL, content_blocks=turn.results))
    return tuple(messages)


def _assistant_text(messages: Sequence[Message]) -> str:
    return "".join(message.text for message in messages if message.role is Role.ASSISTANT)


def _tool_use_ids(messages: Sequence[Message]) -> tuple[str, ...]:
    return tuple(
        block.id
        for message in messages
        for block in message.content_blocks
        if isinstance(block, ToolUse)
    )


def _result_keys(messages: Sequence[Message]) -> tuple[tuple[str, bool], ...]:
    return tuple(
        (block.tool_use_id, block.is_error)
        for message in messages
        for block in message.content_blocks
        if isinstance(block, ToolResultBlock)
    )


def _is_suffix(folded: Sequence[object], recorded: Sequence[object]) -> bool:
    """Whether the folded sequence is the tail of the recorded one (or equal)."""
    return len(folded) <= len(recorded) and tuple(recorded[len(recorded) - len(folded):]) == tuple(
        folded
    )


def _reconcile(folded: Sequence[Message], recorded: Sequence[Message]) -> tuple[str, ...]:
    """Where the fold and the recorded state disagree about the same facts.

    Deliberately structural. Tool-result *content* is not compared: the loop
    truncates it on the way into the transcript while ``ToolEnd`` carries it whole,
    so a content comparison would report a difference on every long result and
    train the reader to ignore this list.
    """
    problems: list[str] = []
    folded_ids, recorded_ids = _tool_use_ids(folded), _tool_use_ids(recorded)
    if not _is_suffix(folded_ids, recorded_ids):
        problems.append(
            f"tool-use ids differ: events replayed {list(folded_ids)}, which is not "
            f"the tail of the recorded state's {list(recorded_ids)}"
        )
    folded_results, recorded_results = _result_keys(folded), _result_keys(recorded)
    if not _is_suffix(folded_results, recorded_results):
        problems.append(
            f"tool results differ: events replayed {list(folded_results)}, which is "
            f"not the tail of the recorded state's {list(recorded_results)} "
            f"(id, is_error)"
        )
    folded_text, recorded_text = _assistant_text(folded), _assistant_text(recorded)
    if not recorded_text.endswith(folded_text):
        problems.append(
            f"assistant text differs: replayed {len(folded_text)} chars are not the "
            f"tail of the recorded {len(recorded_text)} chars — a mishandled "
            f"StreamReset shows up here as duplicated prose"
        )
    return tuple(problems)


def _repair_pairing(
    state: AgentState, known: dict[str, ToolResultBlock]
) -> tuple[AgentState, tuple[str, ...]]:
    """Answer every unpaired tool call so the transcript stays sendable."""
    missing = unpaired_tool_uses(state.messages)
    if not missing:
        return state, ()
    blocks: list[ToolResultBlock] = []
    notes: list[str] = []
    for tool_use_id in missing:
        recorded = known.get(tool_use_id)
        if recorded is not None:
            blocks.append(recorded)
            notes.append(
                f"{tool_use_id}: answered from the recorded ToolEnd (the crash "
                f"landed after the tool returned but before the turn was checkpointed)"
            )
        else:
            blocks.append(
                ToolResultBlock(
                    tool_use_id=tool_use_id, content=INTERRUPTED_NO_RESULT, is_error=True
                )
            )
            notes.append(f"{tool_use_id}: synthesized an interrupted, no-result block")
    repaired = state.with_message(Message(role=Role.TOOL, content_blocks=tuple(blocks)))
    return repaired, tuple(notes)


def _last_checkpoint(turns: Sequence[ReplayTurn]) -> tuple[int, AgentState] | None:
    """The newest turn whose ``TurnEnd`` carried a state, with its position."""
    found: tuple[int, AgentState] | None = None
    for position, turn in enumerate(turns):
        if turn.end is not None and turn.end.agent_state is not None:
            found = (position, turn.end.agent_state)
    return found


def replay(events: Sequence[Event]) -> ReplayResult:
    """Fold a recorded stream into a state that can be sent to a provider as-is."""
    turns = fold_turns(events)
    checkpoint = _last_checkpoint(turns)

    notes: list[str] = []
    mismatches: tuple[str, ...] = ()
    if checkpoint is None:
        source = StateSource.FOLDED
        state = AgentState(messages=_folded_messages(turns))
        notes.append(
            "no TurnEnd carried an agent_state, so the state was folded from events "
            "alone: budget, mode, cwd, todos and thinking signatures are not "
            "recoverable and are left at their defaults"
        )
    else:
        source = StateSource.RECORDED
        position, recorded = checkpoint
        mismatches = _reconcile(
            _folded_messages(turns[: position + 1]), recorded.messages
        )
        tail = _folded_messages(turns[position + 1 :])
        state = replace(recorded, messages=(*recorded.messages, *tail))
        if tail:
            notes.append(
                f"{len(turns) - position - 1} turn(s) recorded after the last "
                f"checkpoint were folded on top of it ({len(tail)} message(s)); the "
                f"session crashed mid-turn, which is why the checkpoint is behind "
                f"the end of the file"
            )

    known = {
        event.tool_use_id: event.result.as_block(event.tool_use_id)
        for event in events
        if isinstance(event, ToolEnd)
    }
    state, repairs = _repair_pairing(state, known)
    if state.pairing_errors:  # pragma: no cover - the repair above is exhaustive
        raise ReplayError(
            f"replayed state still has unpaired tool-use ids "
            f"{list(state.pairing_errors)}; it cannot be sent to a provider"
        )
    return ReplayResult(
        state=state,
        source=source,
        turns=turns,
        mismatches=mismatches,
        repairs=repairs,
        notes=tuple(notes),
    )


# --------------------------------------------------------------------------- #
# Picking a session
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ResumedSession:
    """One session, loaded and replayed, with the read's caveats attached."""

    meta: SessionMeta
    events: tuple[Event, ...]
    replay: ReplayResult
    skipped: tuple[str, ...] = ()

    @property
    def state(self) -> AgentState:
        return self.replay.state


def same_cwd(left: str, right: str) -> bool:
    """Whether two recorded cwds are the same directory.

    Compared by real path so a symlinked checkout, a trailing slash, or ``.``
    versus an absolute path do not split one directory's history into two.
    """
    return os.path.realpath(left) == os.path.realpath(right)


def sessions_for_cwd(directory: Path, cwd: str) -> tuple[SessionMeta, ...]:
    """The picker's rows for ``ronin --resume``, newest first, this directory only."""
    return tuple(meta for meta in list_sessions(directory) if same_cwd(meta.cwd, cwd))


def load_session(path: Path) -> tuple[ReadResult, ReplayResult]:
    """Read one transcript file and replay it. The seam tests exercise directly."""
    read = read_events(path)
    return read, replay(read.events)


def resume(directory: Path, session_id: str) -> ResumedSession:
    """Load and replay one named session — what ``--resume <id>`` calls."""
    path = session_path(directory, session_id)
    read, replayed = load_session(path)
    meta = read.header or SessionMeta(session_id=session_id)
    return ResumedSession(
        meta=replace(meta, session_id=session_id),
        events=read.events,
        replay=replayed,
        skipped=read.skipped,
    )


def resume_latest(directory: Path, cwd: str) -> ResumedSession | None:
    """``ronin --continue``: the newest session recorded in ``cwd``, or ``None``.

    ``None`` rather than an empty session, so the caller can say "nothing to
    continue here" instead of silently starting a fresh conversation that looks
    like a resumed one.
    """
    rows = sessions_for_cwd(directory, cwd)
    if not rows:
        return None
    return resume(directory, rows[0].session_id)


__all__ = [
    "INTERRUPTED_NO_RESULT",
    "ReplayError",
    "ReplayResult",
    "ReplayTurn",
    "ResumedSession",
    "StateSource",
    "fold_turns",
    "load_session",
    "replay",
    "resume",
    "resume_latest",
    "same_cwd",
    "sessions_for_cwd",
]
