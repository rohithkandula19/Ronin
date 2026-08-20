"""The CI / scripting path: ``ronin -p "…" --output-format=stream-json``.

Same event stream as the TUI, no terminal. Three output formats:

``stream-json``
    One JSON object per line, **flushed per event**, so ``ronin -p … | jq`` shows
    progress while the turn is still running. A final ``result`` record closes the
    stream.
``json``
    Nothing until the end, then one ``result`` object.
``text``
    The final answer only, on stdout. Notices (a denied approval, an error) go to
    the *error* writer, so a consumer piping stdout gets the answer and nothing
    else.

**Approval is denied, never granted.** :class:`HeadlessPolicy` answers every
gated call with ``DENY_UNATTENDED`` and records it, and the run exits ``2``. There
is no flag in this module that turns that off: an unattended run that
auto-approves a destructive tool is the failure this whole layer exists to make
impossible.

Exit codes, in precedence order — an error outranks a denial, because an errored
run is the more severe outcome and a script that checks ``!= 0`` must see both:

===== ==========================================================================
``0`` the turn ended ``DONE`` with no errors and no approval requests
``1`` an ``Error`` event, a turn ending ``ERROR``/``INTERRUPTED``, or a stream
      that ended without a ``TurnEnd`` at all (a truncated stream is a failure)
``2`` no error, but at least one ``ApprovalRequest`` was raised and denied
===== ==========================================================================

The JSON schema is a contract: a CI consumer parses it, so an undocumented field
change is a breaking change. Every record carries ``"type"``, and
:data:`SCHEMA` is the machine-readable copy of this table — ``tests/ui`` asserts
the two agree and that every member of the ``Event`` union appears.

==================== =======================================================================
``type``             fields
==================== =======================================================================
``turn_start``       ``turn_index`` int, ``state`` str
``text_delta``       ``text`` str, ``thinking`` bool
``stream_reset``     ``reason`` str
``tool_start``       ``tool_use_id`` str, ``name`` str, ``arguments`` object
``tool_end``         ``tool_use_id`` str, ``name`` str, ``ok`` bool, ``content`` str,
                     ``error`` str, ``artifacts`` array of str, ``tokens_estimate`` int
``tool_output``      ``tool_use_id`` str, ``chunk`` str — output a still-running tool has
                     printed; the complete text still arrives on ``tool_end``
``approval_request`` ``tool_use_id`` str, ``name`` str, ``danger_level`` str,
                     ``danger_rank`` int, ``rendered`` str, ``reason`` str
``turn_end``         ``turn_index`` int, ``state`` str, ``stop_reason`` str,
                     ``has_agent_state`` bool
``compaction``       ``folded_messages`` int, ``token_estimate_before`` int,
                     ``token_estimate_after`` int, ``reason`` str,
                     ``summarizer_failed`` bool
``verify_result``    ``ran`` bool, ``passed`` bool, ``checks_passed`` int,
                     ``checks_failed`` int, ``summary`` str, ``repaired`` bool
``error``            ``message`` str, ``kind`` str, ``recoverable`` bool
``result``           ``exit_code`` int, ``text`` str, ``stop_reason`` str, ``turns`` int,
                     ``resets`` int, ``cost_usd`` float, ``tokens`` int,
                     ``approvals_denied`` array of {``tool_use_id``, ``name``,
                     ``danger_level``, ``rendered``}, ``errors`` array of
                     {``message``, ``kind``, ``recoverable``}
==================== =======================================================================

``turn_end`` deliberately reports ``has_agent_state`` rather than the state
itself: ``AgentState`` contains provider-opaque values that are not portable JSON,
and a consumer that wants to resume uses the session store, not this stream.
"""

from __future__ import annotations

import json
import sys
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from ronin.core.types import (
    DENY_UNATTENDED,
    ApprovalDecision,
    ApprovalRequest,
    Budget,
    Compaction,
    Error,
    Event,
    StreamReset,
    TextDelta,
    ToolEnd,
    ToolOutput,
    ToolSpec,
    ToolStart,
    ToolUse,
    TurnEnd,
    TurnStart,
    TurnState,
    VerifyResult,
)

from .reduce import ViewState, reduce_event
from .render import strip_controls

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NEEDS_APPROVAL = 2

RESULT_TYPE = "result"
DENIAL_NOTICE = "DENIED (non-interactive, no human attached): {name} — {rendered}"
ERROR_NOTICE = "ERROR [{kind}]: {message}"
NO_TURN_END = "stream ended without a TurnEnd"


class OutputFormat(StrEnum):
    """``--output-format``. The values are the CLI spellings."""

    STREAM_JSON = "stream-json"
    JSON = "json"
    TEXT = "text"


#: The documented schema, machine-readable. Keys are ``type`` values; values are
#: the field names that record always carries.
SCHEMA: Mapping[str, tuple[str, ...]] = {
    "turn_start": ("turn_index", "state"),
    "text_delta": ("text", "thinking"),
    "stream_reset": ("reason",),
    "tool_start": ("tool_use_id", "name", "arguments"),
    "tool_end": (
        "tool_use_id",
        "name",
        "ok",
        "content",
        "error",
        "artifacts",
        "tokens_estimate",
    ),
    "approval_request": (
        "tool_use_id",
        "name",
        "danger_level",
        "danger_rank",
        "rendered",
        "reason",
    ),
    "tool_output": ("tool_use_id", "chunk"),
    "turn_end": ("turn_index", "state", "stop_reason", "has_agent_state"),
    "compaction": (
        "folded_messages",
        "token_estimate_before",
        "token_estimate_after",
        "reason",
        "summarizer_failed",
    ),
    "verify_result": (
        "ran",
        "passed",
        "checks_passed",
        "checks_failed",
        "summary",
        "repaired",
    ),
    "error": ("message", "kind", "recoverable"),
    RESULT_TYPE: (
        "exit_code",
        "text",
        "stop_reason",
        "turns",
        "resets",
        "cost_usd",
        "tokens",
        "approvals_denied",
        "errors",
    ),
}

#: Which ``type`` each event class serializes to. A new member of the ``Event``
#: union with no entry here fails a test rather than silently dropping out of the
#: stream a CI job is parsing.
EVENT_TYPE_NAMES: Mapping[type, str] = {
    TurnStart: "turn_start",
    TextDelta: "text_delta",
    StreamReset: "stream_reset",
    ToolStart: "tool_start",
    ToolEnd: "tool_end",
    ToolOutput: "tool_output",
    ApprovalRequest: "approval_request",
    Compaction: "compaction",
    VerifyResult: "verify_result",
    TurnEnd: "turn_end",
    Error: "error",
}


def _flush_stdout() -> None:
    """The default flusher. A named function rather than a lambda so the call site
    stays typed under ``--strict``."""
    sys.stdout.flush()


def _jsonable(value: object) -> object:
    """Coerce a provider-supplied argument value into something ``json`` accepts."""
    if isinstance(value, str | bool | int | float) or value is None:
        return value
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]
    return repr(value)


def event_to_json(event: Event) -> dict[str, Any]:
    """One event as a plain dict matching :data:`SCHEMA`. Total over the union."""
    if isinstance(event, TurnStart):
        return {"type": "turn_start", "turn_index": event.turn_index, "state": event.state.value}
    if isinstance(event, TextDelta):
        return {"type": "text_delta", "text": event.text, "thinking": event.thinking}
    if isinstance(event, StreamReset):
        return {"type": "stream_reset", "reason": event.reason}
    if isinstance(event, ToolStart):
        return {
            "type": "tool_start",
            "tool_use_id": event.tool_use_id,
            "name": event.name,
            "arguments": {str(key): _jsonable(value) for key, value in event.arguments.items()},
        }
    if isinstance(event, ToolEnd):
        return {
            "type": "tool_end",
            "tool_use_id": event.tool_use_id,
            "name": event.name,
            "ok": event.result.ok,
            "content": event.result.content,
            "error": event.result.error,
            "artifacts": list(event.result.artifacts),
            "tokens_estimate": event.result.tokens_estimate,
        }
    if isinstance(event, ApprovalRequest):
        return {
            "type": "approval_request",
            "tool_use_id": event.tool_use_id,
            "name": event.name,
            "danger_level": event.danger_level.name.lower(),
            "danger_rank": int(event.danger_level),
            "rendered": event.rendered,
            "reason": event.reason,
        }
    if isinstance(event, TurnEnd):
        return {
            "type": "turn_end",
            "turn_index": event.turn_index,
            "state": event.state.value,
            "stop_reason": event.stop_reason,
            "has_agent_state": event.agent_state is not None,
        }
    if isinstance(event, Compaction):
        return {
            "type": "compaction",
            "folded_messages": event.folded_messages,
            "token_estimate_before": event.token_estimate_before,
            "token_estimate_after": event.token_estimate_after,
            "reason": event.reason,
            "summarizer_failed": event.summarizer_failed,
        }
    if isinstance(event, VerifyResult):
        return {
            "type": "verify_result",
            "ran": event.ran,
            "passed": event.passed,
            "checks_passed": event.checks_passed,
            "checks_failed": event.checks_failed,
            "summary": event.summary,
            "repaired": event.repaired,
        }
    if isinstance(event, ToolOutput):
        # Live output, emitted losslessly so a scripted consumer can follow a long
        # command as it runs. The complete text still arrives on the tool's ToolEnd,
        # so a reader that ignores this type loses nothing but the liveness.
        return {
            "type": "tool_output",
            "tool_use_id": event.tool_use_id,
            "chunk": event.chunk,
        }
    return {
        "type": "error",
        "message": event.message,
        "kind": event.kind,
        "recoverable": event.recoverable,
    }


def to_line(payload: Mapping[str, Any]) -> str:
    """One record as one line. ``sort_keys`` so a diff of two runs is readable."""
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, default=repr) + "\n"


#: What ``ronin.core.loop`` prefixes a refused tool result with. It is the only signal
#: that separates an approval which was *refused* from one that was *granted*: the
#: request event is emitted before the policy is consulted, so the event itself cannot
#: say which happened.
DENIED_RESULT_PREFIX = "DENIED:"


@dataclass(slots=True)
class ApprovalTracker:
    """Which approvals were actually refused, as opposed to merely requested.

    ``core.loop`` yields ``ApprovalRequest`` *before* it calls ``policy.approve``,
    because a UI has to render the prompt in order to ask. Counting every request made
    exit code 2 mean "this run touched a gated tool" rather than "this run was refused"
    — and it did, for every ``--mode auto_edit`` run that wrote a file: the write was
    allowed by a rule, succeeded with ``ok=True``, and the process still exited 2 with
    that write listed under ``approvals_denied``. A script cannot act on an exit code
    that means two different things, which made 2 useless as the signal it exists to be.

    A request is resolved by its answering ``ToolEnd``: a ``DENIED:`` error means
    refused, anything else means it ran.
    """

    pending: dict[str, ApprovalRequest] = field(default_factory=dict)
    denied: list[ApprovalRequest] = field(default_factory=list)

    def observe(self, event: Event) -> None:
        if isinstance(event, ApprovalRequest):
            self.pending[event.tool_use_id] = event
        elif isinstance(event, ToolEnd):
            request = self.pending.pop(event.tool_use_id, None)
            if request is not None and event.result.error.startswith(DENIED_RESULT_PREFIX):
                self.denied.append(request)

    def resolve(self) -> tuple[ApprovalRequest, ...]:
        """Refusals, plus any request that never got a ``ToolEnd``.

        An unanswered prompt is not an approval. A stream that stopped while waiting did
        not do the work, and exiting 0 there would tell a script the task was done.
        """
        return (*self.denied, *self.pending.values())


def exit_code_for(state: ViewState, approvals: Sequence[ApprovalRequest]) -> int:
    """The process exit code, as a pure function of what the stream said."""
    if state.errors:
        return EXIT_ERROR
    # Anything other than DONE is a failure, which covers ERROR, INTERRUPTED, and
    # a stream that stopped mid-turn: a truncated stream must not exit 0.
    if state.turn_state is not TurnState.DONE:
        return EXIT_ERROR
    if approvals:
        return EXIT_NEEDS_APPROVAL
    return EXIT_OK


@dataclass(frozen=True, slots=True)
class HeadlessResult:
    """What the run produced, for a caller that wants more than an exit code."""

    exit_code: int
    text: str
    state: ViewState
    approvals: tuple[ApprovalRequest, ...] = ()

    @property
    def ok(self) -> bool:
        return self.exit_code == EXIT_OK


def result_record(result: HeadlessResult) -> dict[str, Any]:
    """The closing ``result`` record, in both ``stream-json`` and ``json``."""
    state = result.state
    return {
        "type": RESULT_TYPE,
        "exit_code": result.exit_code,
        "text": result.text,
        "stop_reason": state.stop_reason or (NO_TURN_END if not state.finished else ""),
        "turns": state.turn_index + 1 if state.turn_state is not None else 0,
        "resets": state.resets,
        "cost_usd": state.cost_usd,
        "tokens": state.tokens,
        "approvals_denied": [
            {
                "tool_use_id": request.tool_use_id,
                "name": request.name,
                "danger_level": request.danger_level.name.lower(),
                "rendered": request.rendered,
            }
            for request in result.approvals
        ],
        "errors": [
            {"message": error.message, "kind": error.kind, "recoverable": error.recoverable}
            for error in state.errors
        ],
    }


async def run_headless(
    events: AsyncIterator[Event],
    *,
    output_format: OutputFormat = OutputFormat.STREAM_JSON,
    write: Callable[[str], None] | None = None,
    write_error: Callable[[str], None] | None = None,
    flush: Callable[[], None] | None = None,
    state: ViewState | None = None,
) -> HeadlessResult:
    """Consume the stream, emit the chosen format, and report an exit code.

    Writers are injected so a test captures output without touching a real stream;
    the defaults are stdout and stderr. ``flush`` is called after every
    ``stream-json`` line — a consumer piping the output must not wait on a block
    buffer to see the turn progress.
    """
    out = sys.stdout.write if write is None else write
    err = sys.stderr.write if write_error is None else write_error
    do_flush = _flush_stdout if flush is None else flush

    current = ViewState() if state is None else state
    tracker = ApprovalTracker()
    streaming = output_format is OutputFormat.STREAM_JSON

    async for event in events:
        current = reduce_event(current, event)
        tracker.observe(event)
        if streaming:
            out(to_line(event_to_json(event)))
            do_flush()

    result = HeadlessResult(
        exit_code=exit_code_for(current, tracker.resolve()),
        text=current.text,
        state=current,
        approvals=tracker.resolve(),
    )

    if output_format is OutputFormat.TEXT:
        # `text` goes to a terminal as-is, so it gets the same treatment a renderer
        # would give it. The JSON formats need no equivalent: encoding turns an escape
        # into its six-character JSON form, which is inert by the time anything prints it.
        answer = strip_controls(result.text)
        if answer:
            out(answer if answer.endswith("\n") else answer + "\n")
    else:
        out(to_line(result_record(result)))
        do_flush()

    # Notices never go to stdout: a consumer of `--output-format=text` is piping
    # the model's answer somewhere, and a warning mixed into it corrupts the data.
    #
    # Stripped for every format, including the JSON ones. These are prose on stderr
    # rather than part of the machine-readable stream, so nothing encodes them on the
    # way out — and `DENIAL_NOTICE` carries `rendered`, the command a human is being
    # told was refused, which is precisely the string that must not be able to move a
    # cursor.
    for request in result.approvals:
        err(
            DENIAL_NOTICE.format(name=request.name, rendered=strip_controls(request.rendered))
            + "\n"
        )
    for error in current.errors:
        err(ERROR_NOTICE.format(kind=error.kind, message=strip_controls(error.message)) + "\n")
    if not current.finished:
        err(NO_TURN_END + "\n")
    return result


@dataclass(slots=True)
class HeadlessPolicy:
    """The non-interactive policy: deny every approval, and remember that you did.

    Mutable because recording is the point — the caller needs the list of denials
    to report them, and threading an immutable accumulator through the loop's
    ``Policy`` protocol is not possible (the protocol returns only a decision).

    ``budget`` is the ceiling to enforce; ``cancel()`` exists so a signal handler
    can stop a run without the loop knowing what a signal is.
    """

    budget: Budget = field(default_factory=Budget)
    denials: list[tuple[str, str]] = field(default_factory=list)
    _cancelled: bool = False

    async def approve(self, spec: ToolSpec, use: ToolUse, *, rendered: str) -> ApprovalDecision:
        """Always deny. There is no code path here that returns ``approved=True``."""
        self.denials.append((spec.name, rendered))
        return DENY_UNATTENDED

    def check_budget(self, budget: Budget) -> str | None:
        limits = self.budget
        if limits.max_tokens is not None and budget.spent_tokens >= limits.max_tokens:
            return "token_budget"
        if limits.max_usd is not None and budget.spent_usd >= limits.max_usd:
            return "cost_budget"
        if (
            limits.max_wall_seconds is not None
            and budget.elapsed_seconds >= limits.max_wall_seconds
        ):
            return "wall_budget"
        return None

    def cancelled(self) -> bool:
        return self._cancelled

    def cancel(self) -> None:
        self._cancelled = True


if TYPE_CHECKING:
    # Proof that the headless policy is a Policy the loop will accept, checked by
    # mypy inside this package rather than by a test that only sees method names.
    from ronin.core.protocols import Policy

    _POLICY_CHECK: Policy = HeadlessPolicy()


__all__ = [
    "DENIAL_NOTICE",
    "DENIED_RESULT_PREFIX",
    "ERROR_NOTICE",
    "EVENT_TYPE_NAMES",
    "EXIT_ERROR",
    "EXIT_NEEDS_APPROVAL",
    "EXIT_OK",
    "NO_TURN_END",
    "RESULT_TYPE",
    "SCHEMA",
    "ApprovalTracker",
    "HeadlessPolicy",
    "HeadlessResult",
    "OutputFormat",
    "event_to_json",
    "exit_code_for",
    "result_record",
    "run_headless",
    "to_line",
]
