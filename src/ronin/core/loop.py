"""The ReAct loop: a pure async generator with zero provider and zero UI knowledge.

Everything the loop can talk to arrives through a protocol
(:mod:`ronin.core.protocols`), so a scripted fake replaces a real model with no
network and no monkeypatching. Everything it says leaves as an ``Event``.

**All state changes go through one place.** ``_advance`` is the only function that
produces a new :class:`AgentState`; nothing else in this module rebinds it, and
there are no module-level mutables. The final state rides out on ``TurnEnd`` so a
consumer can resume from it without reaching into the loop.

Four decisions were made here rather than guessed silently — they are recorded in
``docs/ARCHITECTURE.md`` §8 and §9 with their alternatives:

1. **Approval is answered by the injected policy**, not by the consumer through
   the stream, because the work order injects ``policy`` and types the return as
   ``AsyncIterator[Event]``. ``ApprovalRequest`` is still emitted so a UI can show
   what is being decided; it is informational.
2. **The stall nudge is a system-role message** appended to the transcript. The
   loop stays provider-agnostic: rendering a mid-transcript system message for an
   API whose ``system`` is a separate parameter is the adapter's job.
3. **Interrupt is cooperative *and* cancellation-safe.** ``policy.cancelled()`` is
   polled at every await point, which is what lets the loop stay in control and
   emit a well-formed ``ToolEnd``/``TurnEnd``; a hard ``task.cancel()`` is also
   caught around tool execution so the synthetic result still reaches the
   transcript before the ``CancelledError`` propagates. Both are required by the
   spec ("cancellable at any await point" *and* "conversation stays well-formed"),
   so this is one design meeting two constraints, not two competing designs.
4. **Steering lands at the top of an iteration**, never mid-tool-chain. A user
   message between a ``tool_use`` and its ``tool_result`` is a transcript providers
   reject, and "it takes effect at the model's next decision" is a promise that can
   be kept, where "within a second or two" is not. See :mod:`ronin.core.steering`.
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from collections.abc import AsyncIterator, Callable, Iterable, Sequence
from contextlib import suppress
from dataclasses import replace
from enum import StrEnum

from .protocols import (
    FinalMessage,
    ModelClient,
    Policy,
    ResetChunk,
    StreamingToolRegistry,
    TextChunk,
    ToolRegistry,
)
from .types import (
    AgentState,
    ApprovalRequest,
    Budget,
    DangerLevel,
    Error,
    Event,
    Message,
    Role,
    StreamReset,
    Text,
    TextDelta,
    Todo,
    ToolEnd,
    ToolOutput,
    ToolResult,
    ToolSpec,
    ToolStart,
    ToolUse,
    TurnEnd,
    TurnStart,
    TurnState,
)

#: What a steered message is marked with in the transcript. A user message either way —
#: this only says *how* it arrived, so a reader (and a future rewind) can tell a
#: correction typed mid-turn from the prompt that opened the turn.
STEER_KIND = "steer"

DEFAULT_MAX_ITERATIONS = 100
DEFAULT_MAX_TOOL_RESULT_CHARS = 16_000
#: How the model-facing cut divides its budget when a result does not fit. The head
#: holds what a file or a listing opens with; the tail holds why a command failed.
#: Head-biased because most results are reads, but never head-*only*: see
#: ``truncate_for_model``.
TRUNCATE_HEAD_SHARE = 0.6

#: Stall window: the same call fingerprint this many times…
STALL_REPEATS = 3
#: …within a rolling window of this many calls.
STALL_WINDOW = 6

NUDGE = (
    "You have repeated the same action {n} times without progress. State what you "
    "are stuck on and try a different approach."
)

INTERRUPTED_ERROR = "interrupted by user"
STALL_ABORTED = "not run: the turn was aborted because the model stalled"

#: How often a running tool is checked against the cancel flag. The cooperative check at
#: loop boundaries cannot help a command that runs for minutes, so the tool task is polled
#: at this interval instead — short enough that `esc` feels immediate, long enough that the
#: poll is not the reason a turn is slow.
CANCEL_POLL_SECONDS = 0.05


class StopReason(StrEnum):
    """Why a turn ended. Every one is reachable and separately tested."""

    NO_TOOL_CALLS = "no_tool_calls"
    MAX_ITERATIONS = "max_iterations"
    TOKEN_BUDGET = "token_budget"
    COST_BUDGET = "cost_budget"
    INTERRUPTED = "interrupted"
    STALLED = "stalled"


class StalledError(RuntimeError):
    """The model repeated itself after already being nudged.

    Raised rather than returned: a stall is a failure of the *run*, not the
    outcome of a tool, so it does not travel as a ``ToolResult``.

    It carries ``agent_state`` because an abort is still a checkpoint — the
    transcript up to the stall is valid, and throwing it away would make the one
    failure mode users most want to inspect the one they cannot.
    """

    def __init__(
        self, fingerprint: str, repeats: int, agent_state: AgentState | None = None
    ) -> None:
        super().__init__(
            f"stalled: the same action repeated {repeats} times after a nudge "
            f"(fingerprint {fingerprint})"
        )
        self.fingerprint = fingerprint
        self.repeats = repeats
        self.agent_state = agent_state


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #


def truncate_for_model(content: str, limit: int = DEFAULT_MAX_TOOL_RESULT_CHARS) -> str:
    """Deterministically cut ``content`` and say exactly what was removed.

    The marker is part of the contract: the model must be able to tell the
    difference between "the file ends here" and "we stopped showing you the file".

    Both ends survive, because this is the *last* cut before the model sees the
    result and it is smaller than every cap above it — so whatever shape a tool
    chose for its own output, this is the shape that reaches the model. A tool
    that clamped itself kept the tail on purpose (a traceback is at the end, not
    the start); a head-only cut here would discard exactly the part that says why
    the command failed, and would leave that tool's "head and tail kept" marker
    asserting something no longer true.
    """
    if limit <= 0 or len(content) <= limit:
        return content
    head_chars = int(limit * TRUNCATE_HEAD_SHARE)
    tail_chars = limit - head_chars
    head = content[:head_chars]
    tail = content[len(content) - tail_chars :]
    cut_chars = len(content) - limit
    cut_lines = content.count("\n") - head.count("\n") - tail.count("\n")
    return (
        f"{head}\n…[truncated: {cut_chars} chars, {cut_lines} lines cut from the "
        f"middle; head and tail kept]\n{tail}"
    )


def fingerprint(use: ToolUse) -> str:
    """A stable identity for "the same action", for stall detection.

    Arguments are normalized by sorting keys, so ``{"a":1,"b":2}`` and
    ``{"b":2,"a":1}`` are the same action. Unserializable values fall back to
    ``repr``, which is still stable within a process.
    """
    try:
        args = json.dumps(dict(use.arguments), sort_keys=True, default=repr)
    except (TypeError, ValueError):  # pragma: no cover - default=repr covers this
        args = repr(sorted(use.arguments.items()))
    return f"{use.name}:{args}"


def _all_read_only(specs: Iterable[ToolSpec | None]) -> bool:
    """Parallel execution is only safe when nothing in the batch mutates."""
    resolved = list(specs)
    return bool(resolved) and all(
        spec is not None and spec.danger_level == DangerLevel.READ_ONLY for spec in resolved
    )


def _fold_usage(budget: Budget, final: FinalMessage) -> Budget:
    """Add one model response's reported usage into the budget."""
    return replace(
        budget,
        spent_tokens=budget.spent_tokens + final.input_tokens + final.output_tokens,
        spent_usd=budget.spent_usd + final.cost_usd,
    )


def _advance(
    state: AgentState,
    *,
    messages: Sequence[Message] | None = None,
    budget: Budget | None = None,
    todos: Callable[[], Sequence[Todo]] | None = None,
) -> AgentState:
    """The single place a new :class:`AgentState` is produced.

    ``todos`` is a callable rather than a value because the plan lives outside the loop
    — in the tool context ``todo_write`` writes to — and is read at the moment the state
    is produced, not at the moment the turn started. Passing a value would snapshot it
    before the tools that change it have run.
    """
    return replace(
        state,
        messages=tuple(messages) if messages is not None else state.messages,
        budget=budget if budget is not None else state.budget,
        todos=tuple(todos()) if todos is not None else state.todos,
    )


def _interrupted_result() -> ToolResult:
    return ToolResult(ok=False, error=INTERRUPTED_ERROR)


def _results_message(pairs: Sequence[tuple[ToolUse, ToolResult]], limit: int) -> Message:
    """One tool-role message answering every call in the batch, in order.

    Answering the whole batch in one message keeps the pairing invariant trivially
    satisfied: the ids that went out in one assistant message come back together.
    """
    blocks = tuple(
        replace(
            result.as_block(use.id),
            content=truncate_for_model(result.model_text(), limit),
        )
        for use, result in pairs
    )
    return Message(role=Role.TOOL, content_blocks=blocks)


# --------------------------------------------------------------------------- #
# The loop
# --------------------------------------------------------------------------- #


async def run_turn(
    state: AgentState,
    model: ModelClient,
    tools: ToolRegistry,
    policy: Policy,
    *,
    system: str = "",
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    max_tool_result_chars: int = DEFAULT_MAX_TOOL_RESULT_CHARS,
    steering: Callable[[], Sequence[str]] | None = None,
    todos: Callable[[], Sequence[Todo]] | None = None,
    preview: Callable[[ToolUse], str | None] | None = None,
) -> AsyncIterator[Event]:
    """Run one turn to completion, yielding every observable step.

    Stops for exactly one of :class:`StopReason`, and says which on ``TurnEnd``.
    Raises only :class:`StalledError` (a repeat after a nudge) and
    ``asyncio.CancelledError`` (a hard cancel) — everything else, including a tool
    that raises, becomes a value.

    ``steering`` is the mid-turn correction channel: a callable the loop *pulls*, at
    the top of each iteration, for messages the user typed while this turn was
    running (see :mod:`ronin.core.steering`). It is pulled rather than pushed for the
    same reason ``policy.cancelled()`` is — the loop stays a generator over injected
    values, with nothing to receive and no second task to coordinate with. Unset, the
    loop behaves exactly as it did before there was such a thing as steering.

    ``todos`` is the model's plan, pulled the same way and for the same reason: it lives
    in the tool context that ``todo_write`` writes to, which the loop cannot see through
    the ``ToolRegistry`` protocol. Without it ``AgentState.todos`` was empty on every
    live turn — so the plan never reached the transcript, and a resumed session came
    back with an empty checklist and a model that had been told nothing was in progress.
    """
    specs = list(tools.specs())
    messages: list[Message] = list(state.messages)
    budget = state.budget
    recent: deque[str] = deque(maxlen=STALL_WINDOW)
    nudged = False
    index = 0

    for index in range(max_iterations):
        yield TurnStart(turn_index=index)

        if policy.cancelled():
            state = _advance(state, messages=messages, budget=budget, todos=todos)
            yield TurnEnd(
                turn_index=index,
                state=TurnState.INTERRUPTED,
                stop_reason=StopReason.INTERRUPTED.value,
                agent_state=state,
            )
            return

        if (reason := policy.check_budget(budget)) is not None:
            state = _advance(state, messages=messages, budget=budget, todos=todos)
            yield TurnEnd(
                turn_index=index,
                state=TurnState.DONE,
                stop_reason=reason,
                agent_state=state,
            )
            return

        # ------------------------------------------------------------ steering
        # The one safe seam for a mid-turn correction, and the reason it is here
        # rather than wherever the keystroke landed: every `tool_use` from the
        # previous assistant message has already been answered by the
        # `_results_message` at the bottom of the last iteration, and the next model
        # call has not been made yet. Anywhere inside the tool run would put a user
        # message between a `tool_use` and its `tool_result`, which every provider
        # rejects — and would also make "when does my correction take effect?"
        # depend on which tool happened to be running.
        #
        # After the cancellation check on purpose. An interrupted turn must not
        # swallow the message: leaving it in the holder is what lets the orchestrator
        # deliver it as the next turn instead of losing it to a turn that is ending.
        if steering is not None:
            for correction in steering():
                # USER, not SYSTEM: this is the human talking, and calling it
                # anything else would misreport who said it to compaction, to a
                # rewind, and to anyone reading the transcript back.
                messages.append(
                    Message(
                        role=Role.USER,
                        content_blocks=(Text(correction),),
                        metadata={"kind": STEER_KIND},
                    )
                )

        # ------------------------------------------------------ model streaming
        final: FinalMessage | None = None
        async for chunk in model.stream(system=system, messages=messages, tools=specs):
            if isinstance(chunk, TextChunk):
                yield TextDelta(text=chunk.text, thinking=chunk.thinking)
            elif isinstance(chunk, ResetChunk):
                yield StreamReset(reason=chunk.reason)
            else:
                final = chunk

        if final is None:
            state = _advance(state, messages=messages, budget=budget, todos=todos)
            yield Error(
                message="model stream ended without a final message",
                kind="protocol",
                recoverable=False,
            )
            yield TurnEnd(
                turn_index=index,
                state=TurnState.ERROR,
                stop_reason="protocol_error",
                agent_state=state,
            )
            return

        messages.append(final.message)
        budget = _fold_usage(budget, final)
        calls = final.message.tool_uses

        # ------------------------------------------------ provider-level failure
        if final.error:
            # The provider says this turn failed — the tool-call shim exhausting
            # its repair budget is the case that motivated this. Surfacing it is
            # not optional: without it a turn whose calls were unparseable looks
            # exactly like a model that chose to answer in prose, and the loop
            # below would end with `DONE`.
            yield Error(message=final.error, kind="provider", recoverable=bool(calls))
            if not calls:
                state = _advance(state, messages=messages, budget=budget, todos=todos)
                yield TurnEnd(
                    turn_index=index,
                    state=TurnState.ERROR,
                    stop_reason="provider_error",
                    agent_state=state,
                )
                return
            # Calls that *did* parse are still real work the model asked for, so
            # execution continues below. The failure has been reported either way.

        # -------------------------------------------------- (a) no tool calls
        if not calls:
            state = _advance(state, messages=messages, budget=budget, todos=todos)
            yield TurnEnd(
                turn_index=index,
                state=TurnState.DONE,
                stop_reason=StopReason.NO_TOOL_CALLS.value,
                agent_state=state,
            )
            return

        # ------------------------------------------------------ (f) stall check
        nudge_for: str | None = None
        for use in calls:
            mark = fingerprint(use)
            recent.append(mark)
            repeats = sum(1 for seen in recent if seen == mark)
            if repeats >= STALL_REPEATS:
                if nudged:
                    # The calls in this batch never ran, so the transcript would
                    # carry unpaired tool_use ids and a provider would reject the
                    # state we just promised was resumable. Answer them, exactly
                    # as an interrupt does, then abort.
                    messages.append(
                        _results_message(
                            [(call, ToolResult(ok=False, error=STALL_ABORTED)) for call in calls],
                            max_tool_result_chars,
                        )
                    )
                    raise StalledError(
                        mark,
                        repeats,
                        _advance(state, messages=messages, budget=budget, todos=todos),
                    )
                nudge_for = mark
        if nudge_for is not None:
            nudged = True
            repeats = sum(1 for seen in recent if seen == nudge_for)
            messages.append(
                Message(
                    role=Role.SYSTEM,
                    content_blocks=(Text(NUDGE.format(n=repeats)),),
                    metadata={"kind": "stall_nudge", "fingerprint": nudge_for},
                )
            )

        # --------------------------------------------------- approval + execute
        resolved = [tools.get(use.name) for use in calls]
        pairs: list[tuple[ToolUse, ToolResult]] = []
        approved: list[tuple[ToolUse, ToolSpec]] = []

        for use, spec in zip(calls, resolved, strict=True):
            yield ToolStart(tool_use_id=use.id, name=use.name, arguments=use.arguments)

            if spec is None:
                pairs.append(
                    (use, ToolResult(ok=False, error=f"tool {use.name!r} is not registered"))
                )
                yield ToolEnd(tool_use_id=use.id, name=use.name, result=pairs[-1][1])
                continue

            if policy.cancelled():
                pairs.append((use, _interrupted_result()))
                yield ToolEnd(tool_use_id=use.id, name=use.name, result=pairs[-1][1])
                continue

            if spec.requires_approval:
                # A diff when one can be built, the call and its arguments otherwise.
                # One value either way: what the event carries, what the policy is
                # asked with and what the human reads are the same string by
                # construction, and that is the property the gate rests on.
                rendered = (preview(use) if preview is not None else None) or _render(use)
                yield ApprovalRequest(
                    tool_use_id=use.id,
                    name=use.name,
                    danger_level=spec.danger_level,
                    rendered=rendered,
                    reason=spec.danger_level.name.lower(),
                )
                decision = await policy.approve(spec, use, rendered=rendered)
                if not decision.approved:
                    detail = decision.reason or "the user declined this action"
                    pairs.append((use, ToolResult(ok=False, error=f"DENIED: {detail}")))
                    yield ToolEnd(tool_use_id=use.id, name=use.name, result=pairs[-1][1])
                    continue

            approved.append((use, spec))

        # Parallel only when every approved call is read-only; serial otherwise.
        if len(approved) > 1 and _all_read_only([spec for _, spec in approved]):
            results = await _execute_parallel(tools, [use for use, _ in approved])
        else:
            results = []
            for use, _spec in approved:
                if policy.cancelled():
                    results.append(_interrupted_result())
                    continue
                # Serial is where the streaming tools are: a batch only runs in
                # parallel when every member is read-only, and the tools that print
                # for minutes (bash) are not.
                produced: list[ToolResult] = []
                async for live in _execute_streaming(
                    tools, use, produced, cancelled=policy.cancelled
                ):
                    yield live
                results.append(produced[0])

        for (use, _spec), result in zip(approved, results, strict=True):
            pairs.append((use, result))
            yield ToolEnd(tool_use_id=use.id, name=use.name, result=result)

        # Order results the way the calls were made, so the transcript reads
        # in the same order the model asked.
        order = {use.id: position for position, use in enumerate(calls)}
        pairs.sort(key=lambda pair: order[pair[0].id])
        messages.append(_results_message(pairs, max_tool_result_chars))

        # ------------------------------------------------------- (e) interrupt
        if policy.cancelled():
            state = _advance(state, messages=messages, budget=budget, todos=todos)
            yield TurnEnd(
                turn_index=index,
                state=TurnState.INTERRUPTED,
                stop_reason=StopReason.INTERRUPTED.value,
                agent_state=state,
            )
            return

    # ------------------------------------------------------ (b) max iterations
    state = _advance(state, messages=messages, budget=budget, todos=todos)
    yield TurnEnd(
        turn_index=index,
        state=TurnState.ERROR,
        stop_reason=StopReason.MAX_ITERATIONS.value,
        agent_state=state,
    )


def _render(use: ToolUse) -> str:
    """What the human is shown, and therefore what they approve."""
    args = json.dumps(dict(use.arguments), sort_keys=True, default=repr)
    return f"{use.name}({args})"


async def _execute_one(
    tools: ToolRegistry, use: ToolUse, *, on_output: Callable[[str], None] | None = None
) -> ToolResult:
    """Execute one call, converting any escape into a value.

    A registry is *supposed* to return a ``ToolResult``; a protocol cannot make it,
    so the boundary is enforced here. ``CancelledError`` is caught so the
    conversation still gets a well-formed answer, then re-raised.

    ``on_output`` is honoured only by a registry that advertises
    :class:`~ronin.core.protocols.StreamingToolRegistry`. Everything else takes the
    plain call and simply does not stream — liveness is never worth failing a tool over.
    """
    try:
        if on_output is not None and isinstance(tools, StreamingToolRegistry):
            return await tools.execute_streaming(use, on_output)
        return await tools.execute(use)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")


async def _execute_streaming(
    tools: ToolRegistry,
    use: ToolUse,
    into: list[ToolResult],
    *,
    cancelled: Callable[[], bool] | None = None,
    poll: float = CANCEL_POLL_SECONDS,
) -> AsyncIterator[ToolOutput]:
    """Run one call, yielding what it prints *while* it prints it.

    The result cannot be returned — an async generator has no return value — so it is
    appended to ``into``. The caller runs this to exhaustion and then reads the single
    element, which keeps the interleaving here and the bookkeeping there.

    The tool runs as a task while a queue collects its chunks, because a generator
    cannot yield from inside an ``await``. Racing the two is what makes output appear
    during the command instead of after it. The queue is drained again after the task
    finishes, so the last lines before a fast exit are not lost.

    ``cancelled`` is polled every ``poll`` seconds *while the tool runs*, and this is the
    only place an interrupt can reach a tool that has already started. The cooperative
    check the rest of the loop uses sits at iteration boundaries, which a command running
    for minutes never reaches — so ``esc`` during a long build used to do nothing visible
    until the command finished on its own.
    """
    queue: asyncio.Queue[str] = asyncio.Queue()
    task = asyncio.create_task(_execute_one(tools, use, on_output=queue.put_nowait))
    getter: asyncio.Task[str] | None = None
    try:
        while True:
            getter = asyncio.ensure_future(queue.get())
            done, _pending = await asyncio.wait(
                (task, getter), timeout=poll, return_when=asyncio.FIRST_COMPLETED
            )
            if getter in done:
                yield ToolOutput(tool_use_id=use.id, chunk=getter.result())
                getter = None
                continue
            getter.cancel()
            getter = None
            if task in done:
                break
            if cancelled is not None and cancelled():
                # Cancelling unwinds the tool; the shell kills its process group on the
                # way out, so the subprocess dies rather than being orphaned.
                task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await task
                into.append(_interrupted_result())
                return
    finally:
        if getter is not None:
            getter.cancel()
    while not queue.empty():
        yield ToolOutput(tool_use_id=use.id, chunk=queue.get_nowait())
    into.append(await task)


async def _execute_parallel(tools: ToolRegistry, uses: Sequence[ToolUse]) -> list[ToolResult]:
    """Run a read-only batch concurrently, preserving order."""
    gathered = await asyncio.gather(
        *(_execute_one(tools, use) for use in uses), return_exceptions=True
    )
    results: list[ToolResult] = []
    for outcome in gathered:
        if isinstance(outcome, asyncio.CancelledError):  # pragma: no cover - see tests
            results.append(_interrupted_result())
        elif isinstance(outcome, BaseException):
            results.append(ToolResult(ok=False, error=f"{type(outcome).__name__}: {outcome}"))
        else:
            results.append(outcome)
    return results


__all__ = [
    "DEFAULT_MAX_ITERATIONS",
    "DEFAULT_MAX_TOOL_RESULT_CHARS",
    "INTERRUPTED_ERROR",
    "NUDGE",
    "STALL_ABORTED",
    "STALL_REPEATS",
    "STALL_WINDOW",
    "STEER_KIND",
    "TRUNCATE_HEAD_SHARE",
    "StalledError",
    "StopReason",
    "fingerprint",
    "run_turn",
    "truncate_for_model",
]
