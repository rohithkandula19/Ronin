"""What has to happen *around* a turn before a turn is a session.

``core.loop.run_turn`` is one ReAct turn and nothing else, which is why it has no
idea what a transcript, a context window, a checkpoint or a test suite is. Five
things have to happen around it, and each one is a bug if it happens in the wrong
place:

1. **Compaction before the turn, counting the pinned prefix.** The transcript is
   folded *before* the request goes out, because a request that is already over the
   window is a 400 no amount of retrying fixes. ``pinned_prefix_tokens`` is passed
   from :meth:`ronin.cli.spine.Loaded.pinned_prefix_tokens` — its default of 0 means
   compaction believes the window is emptier than it is by the size of the repo map
   plus RONIN.md, which is the one part of the prompt that never shrinks. The
   summarizer runs on the **fast** role (``router.for_compaction()``), never on the
   main model. Compaction's own marker is emitted into the event stream, because a
   session that silently forgets the middle of its own history is a session the user
   will think has gone stupid.

2. **A checkpoint before the first mutating call, not before the turn.** Whether a
   turn mutates is not knowable in advance, so the snapshot is taken when the first
   mutating ``ToolStart`` comes past — and because this module is *downstream* of
   ``run_turn``'s async generator, ``run_turn`` is suspended at that ``yield`` and
   the tool has not executed yet. A read-only turn therefore takes no checkpoint at
   all, and a mutating one is covered before the first byte changes. Unavailable
   checkpoints are a note (with the ``git init`` that would fix it), never a failure.

3. **Every event to the transcript, and a write failure that cannot kill the run.**
   ``persistence.transcript`` already fsyncs at the turn boundary. A full disk must
   not end a session that is otherwise working, so the recorder degrades to a note
   and the stream continues — but the user is told, because a session whose recording
   died is a session that cannot be resumed.

4. **Verification after a mutating turn, fed back as a tool result.** The constraint
   is the user's, verbatim: *the loop must not end because the model said "done"*. So
   the changed paths come from the checkpoint diff (not from the model's account of
   what it did), the plan is scoped to them, and the outcome goes back through
   ``verify.runner.as_tool_result`` — a ``ToolResult``, never a user-role message. A
   user turn reads as a new instruction and the model re-scopes to explaining the
   traceback; a tool result reads as an observation about what it just did and it
   keeps working. That distinction is why :func:`as_tool_result` exists, and this is
   the call site it exists for.

5. **A capped repair loop with an honest ending.** ``verify.repair.repair_loop`` owns
   the cap and the "same failure signature three times → stop" rule. When it gives
   up, its report is emitted as an :class:`~ronin.core.types.Error`, which is also
   what makes ``ronin -p`` exit non-zero: a run that could not verify its own edits
   did not succeed, whatever the model said in its last paragraph.

Three session-level actions are surfaced as synthetic ``ToolStart``/``ToolEnd``
pairs named ``compact``, ``checkpoint`` and ``verify``. That is deliberate: the
``Event`` union has no notice channel, and the two alternatives were worse —
``TextDelta`` would splice middleware chatter into the model's answer (which
``--output-format text`` pipes somewhere), and ``Error`` would make a routine
compaction exit 1. A tool pair is honest (these *are* actions with results), lands
in the tools pane of every renderer, survives replay, and stays out of the answer.

Nothing here swallows ``asyncio.CancelledError``: the recorder catches ``OSError``
and ``TranscriptError`` only, and the one background task (the repair loop, which
has to be a task because ``repair_loop`` takes callbacks and cannot yield through
them) is cancelled in a ``finally`` and re-raised from.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field, replace

from ..context.compaction import CompactionResult, Summarizer, maybe_compact
from ..core.loop import StalledError, run_turn
from ..core.protocols import FinalMessage, ModelClient, TextChunk
from ..core.types import (
    AgentState,
    Budget,
    DangerLevel,
    Error,
    Event,
    Message,
    Role,
    Text,
    ToolEnd,
    ToolResult,
    ToolStart,
    ToolUse,
    TurnEnd,
    TurnStart,
    TurnState,
    unpaired_tool_uses,
)
from ..persistence.transcript import TranscriptError
from ..providers.bridge import LoopClient
from ..providers.router import Role as ModelRole
from ..providers.router import Router
from ..verify.checkpoints import Checkpoint, changed_paths_from_diff
from ..verify.repair import (
    DEFAULT_MAX_ATTEMPTS,
    FailureSnapshot,
    RepairReport,
    repair_loop,
)
from ..verify.runner import (
    DEFAULT_COMMAND_TIMEOUT,
    CommandRunner,
    VerifyOutcome,
    VerifyPlan,
    as_tool_result,
    plan_for_changes,
    run_command,
    run_plan,
    should_verify,
)
from .spine import Runtime

#: Matches ``core.loop.DEFAULT_MAX_ITERATIONS``. Restated because it is part of
#: this module's published signature, not borrowed from the loop's defaults.
DEFAULT_MAX_ITERATIONS = 100

#: Names of the three session-level actions surfaced as tool pairs. Prefixed so a
#: real tool called ``verify`` cannot be confused with the middleware's own step.
COMPACT_STEP = "compact"
CHECKPOINT_STEP = "checkpoint"
VERIFY_STEP = "verify"

#: How much of a summary the fast model is allowed to write. A five-section digest
#: that runs past this is not a digest.
SUMMARY_MAX_TOKENS = 2048

_NO_CHECKPOINT = (
    "no checkpoint was taken, so /undo cannot roll this turn back: {detail} "
    "the turn continues — everything else works normally."
)

_SKIPPED_BY_MODE = (
    "verification was not run: in {mode} mode the human approves each edit and a "
    "second opinion they did not ask for is noise. Nothing here was proven by a "
    "test — do not report it as verified."
)

_NO_VERIFY_COMMANDS = (
    "verification could not run: this repo declares no lint, typecheck, test or "
    "build command that applies to {count} changed file(s). Nothing was proven. "
    "Add one to RONIN.md or pyproject/package.json to make this real."
)

_DIFF_UNAVAILABLE = (
    "could not read the checkpoint diff ({detail}), so the changed-file list is "
    "unknown and verification was scoped to nothing"
)


class CompactionPairingError(RuntimeError):
    """Compaction left a tool-use id with no answering result.

    Raised rather than logged, and raised *here* rather than discovered later: an
    unpaired transcript is rejected by the provider on the **next** request, so the
    traceback would point at a turn that is not the one that broke it. See
    ``ronin.core.types.unpaired_tool_uses`` and the pairing discussion in
    ``ronin.context.compaction``.
    """

    def __init__(self, unpaired: Sequence[str]) -> None:
        super().__init__(
            "compaction produced a transcript with unpaired tool-use ids "
            f"{list(unpaired)}; sending it would be a provider 400 on the next turn"
        )
        self.unpaired = tuple(unpaired)


# --------------------------------------------------------------------------- #
# The summarizer seam
# --------------------------------------------------------------------------- #


def summarizer_for(router: Router, *, max_tokens: int = SUMMARY_MAX_TOKENS) -> Summarizer:
    """A :data:`~ronin.context.compaction.Summarizer` on the **fast** model.

    ``router.for_compaction()`` takes no role argument by construction, which is the
    whole point: compacting a 200-turn transcript on the main model costs more than
    the tokens it saves, and a role argument here is a place for that mistake to be
    made once and never noticed.

    Built lazily by :meth:`Conversation.run_prompt` — a session that never reaches
    the trigger never constructs a client, so a workspace with no ``fast`` model
    configured still runs.
    """
    spec = router.spec_for(ModelRole.FAST)
    client = LoopClient(router.for_compaction(), model=spec.model, max_tokens=max_tokens)

    async def summarize(prompt: str) -> str:
        parts: list[str] = []
        fallback = ""
        stream = client.stream(
            system="", messages=(Message(role=Role.USER, content_blocks=(Text(prompt),)),), tools=()
        )
        async for chunk in stream:
            if isinstance(chunk, TextChunk):
                if not chunk.thinking:
                    parts.append(chunk.text)
            elif isinstance(chunk, FinalMessage):
                # Non-streaming adapters deliver the whole answer on the final
                # message; without this a summary arrives empty and compaction
                # falls back to its local digest for no reason.
                fallback = chunk.message.text
        return "".join(parts) or fallback

    return summarize


# --------------------------------------------------------------------------- #
# Plan mode
# --------------------------------------------------------------------------- #


def plan_runtime(runtime: Runtime) -> Runtime:
    """The same runtime with a registry that *cannot* edit. Not a prompt change.

    A model told "you are planning, do not edit" edits three turns later, when the
    instruction has scrolled past the interesting part of the context. So plan mode
    narrows the registry (``agents.plan_mode.plan_registry``) and re-derives the
    guarantee from the specs (``assert_read_only``) rather than trusting the
    narrowing — a mislabelled tool fails here instead of at the first write.

    ``PLAN_MODE_TOOLS`` is intersected with what the session actually has, because
    ``subset`` raises on an unknown name and a workspace built without ``task``
    should still be able to plan.
    """
    from ..agents.plan_mode import (
        PLAN_MODE_PROMPT,
        PLAN_MODE_TOOLS,
        assert_read_only,
        plan_registry,
    )

    base = runtime.session.registry
    names = [name for name in PLAN_MODE_TOOLS if base.get(name) is not None]
    if not names:
        raise ValueError(
            "plan mode needs at least one of "
            f"{list(PLAN_MODE_TOOLS)}, and this session has none of them"
        )
    narrowed = plan_registry(base, names=names)
    assert_read_only(narrowed)
    system = f"{runtime.system}\n\n{PLAN_MODE_PROMPT}" if runtime.system else PLAN_MODE_PROMPT
    return replace(runtime, registry=narrowed, system=system)


# --------------------------------------------------------------------------- #
# Recording
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class _Recorder:
    """Appends every event to the transcript, and survives a transcript that dies.

    Mutable because it counts failures: the user is told **once** that recording
    stopped, not once per event, and a session that emits four hundred identical
    warnings has told them nothing.
    """

    runtime: Runtime
    failures: int = 0
    notes: list[str] = field(default_factory=list)

    def record(self, event: Event) -> Event:
        transcript = self.runtime.transcript
        if transcript is None:
            return event
        try:
            transcript.append(event)
        # Narrow on purpose: OSError is the disk, TranscriptError is a record that
        # will not serialize. CancelledError is a BaseException and is not caught
        # here by construction — an interrupted session must stay interrupted.
        except (OSError, TranscriptError) as exc:
            self.failures += 1
            if self.failures == 1:
                self.notes.append(
                    f"the transcript stopped recording after {exc!r} — the session "
                    "continues, but this session cannot be resumed or exported past "
                    f"this point ({transcript.path})"
                )
        return event


# --------------------------------------------------------------------------- #
# The conversation
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class Conversation:
    """One conversation's accumulated messages, plus the seams a turn needs injected.

    Mutable, and it exists for one reason: :func:`run_prompt`'s signature (fixed in
    ``SEAMS.md``) has nowhere to keep two things a session needs — the messages from
    the previous turns, and the subprocess/clock seams that make a verify pass
    testable offline. ``Runtime`` is a frozen value and holds neither, and a hidden
    module-level registry keyed by runtime would be exactly the kind of invisible
    global the conventions forbid. So the caller holds this, and the free
    :func:`run_prompt` is one turn on a fresh one.

    ``model`` overrides ``runtime.session.main`` — the model seam, so ``tests/cli``
    runs with no provider. ``command`` is the subprocess seam every verify step goes
    through. ``clock`` is the wall-clock seam, and it is not decoration: nothing in
    the loop advances ``Budget.elapsed_seconds``, so ``max_wall_seconds`` would never
    fire unless something folded elapsed time in at the turn boundary. This does.
    """

    messages: tuple[Message, ...] = ()
    budget: Budget = field(default_factory=Budget)
    model: ModelClient | None = None
    command: CommandRunner = run_command
    clock: Callable[[], float] = time.monotonic
    verify_timeout: float = DEFAULT_COMMAND_TIMEOUT
    max_repair_attempts: int = DEFAULT_MAX_ATTEMPTS

    #: What this conversation did, for ``/doctor``, the SDK and the demo.
    notes: list[str] = field(default_factory=list)
    checkpoints: list[Checkpoint] = field(default_factory=list)
    last_compaction: CompactionResult | None = None
    last_verify: VerifyOutcome | None = None
    last_repair: RepairReport | None = None
    #: The value handed to ``maybe_compact``. Kept so a caller can prove the pinned
    #: prefix was counted rather than take it on faith.
    last_pinned_prefix_tokens: int = 0
    compactions: int = 0

    _started_at: float | None = None
    _step: int = 0
    _turn_index: int = 0
    _mutated: bool = False
    _checkpointed: bool = False
    _turn_base: str | None = None

    # ------------------------------------------------------------------ state

    def resume_from(self, state: AgentState) -> Conversation:
        """Adopt a state replayed by ``persistence.resume``. Returns ``self``.

        Takes the whole :class:`~ronin.core.types.AgentState` rather than just its
        messages so the spend comes back with them — a resumed session that forgets
        what it spent has no ceiling. Returns ``self`` so it reads as one expression
        at the construction site.
        """
        self.messages = state.messages
        self.budget = state.budget
        return self

    @property
    def state(self) -> AgentState:
        """The conversation as a resumable value."""
        return AgentState(messages=self.messages, budget=self.budget)

    # -------------------------------------------------------------- the turn

    async def run_prompt(
        self,
        runtime: Runtime,
        prompt: str,
        *,
        budget: Budget | None = None,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        summarizer: Summarizer | None = None,
        verify: bool = True,
    ) -> AsyncIterator[Event]:
        """One user turn, with compaction, checkpointing, recording and verification.

        Yields the same ``Event`` stream ``run_turn`` does, so ``ronin.ui`` — the
        reducer, the renderers, the TUI and ``run_headless`` — consumes it unchanged.
        """
        recorder = _Recorder(runtime)
        if budget is not None:
            self.budget = budget
        if self._started_at is None:
            self._started_at = self.clock()
        # Per *prompt*, not per conversation: without this a read-only follow-up to a
        # mutating turn re-runs the whole verify pass on the previous turn's diff, and
        # reports the previous turn's repair failure a second time.
        self._mutated = False
        self._turn_base = None

        self.messages = (*self.messages, Message(role=Role.USER, content_blocks=(Text(prompt),)))

        async for event in self._compact(runtime, summarizer):
            yield recorder.record(event)

        async for event in self._turn(runtime, max_iterations=max_iterations):
            yield recorder.record(event)

        if verify and self._mutated:
            async for event in self._verify(runtime, max_iterations=max_iterations):
                yield recorder.record(event)

        self.notes.extend(recorder.notes)

    # ------------------------------------------------------------ compaction

    async def _compact(
        self, runtime: Runtime, summarizer: Summarizer | None
    ) -> AsyncIterator[Event]:
        """Fold the middle if the trigger is reached, and say so out loud."""
        pinned = runtime.loaded.pinned_prefix_tokens()
        self.last_pinned_prefix_tokens = pinned
        summarize = summarizer if summarizer is not None else self._summarizer(runtime)
        result = await maybe_compact(
            self.messages,
            policy=runtime.compaction,
            summarizer=summarize,
            pinned_prefix_tokens=pinned,
        )
        self.last_compaction = result
        if not result.compacted:
            return

        unpaired = unpaired_tool_uses(result.messages)
        if unpaired:
            raise CompactionPairingError(unpaired)

        self.messages = result.messages
        self.compactions += 1
        if result.summarizer_error:
            self.notes.append(
                f"the compaction summarizer failed ({result.summarizer_error}); a "
                "locally-computed digest was used instead — it states less but "
                "cannot state anything false"
            )
        if result.still_over_trigger:
            self.notes.append(
                "the transcript is still at or above the compaction trigger after "
                f"folding (~{result.token_estimate_after:,} tokens vs a trigger of "
                f"{result.trigger_tokens:,}): the retained per-path tool results are "
                "themselves bigger than the budget. Compacting again would change "
                "nothing — bound max_retained_paths or start a fresh session."
            )
        step = self._next_step(COMPACT_STEP)
        arguments = {
            "tokens_before": result.token_estimate_before,
            "tokens_after": result.token_estimate_after,
            "folded_messages": result.folded_messages,
        }
        yield ToolStart(tool_use_id=step, name=COMPACT_STEP, arguments=arguments)
        yield ToolEnd(
            tool_use_id=step,
            name=COMPACT_STEP,
            result=ToolResult(
                ok=True,
                content=result.marker,
                artifacts=result.retained_paths,
                tokens_estimate=result.token_estimate_after,
            ),
        )

    def _summarizer(self, runtime: Runtime) -> Summarizer:
        """The default summarizer, built on first use.

        Deferred because constructing it resolves the ``fast`` role to a live client,
        which may load weights or want an API key — work a session that never
        compacts must not pay.
        """
        built: list[Summarizer] = []

        async def summarize(prompt: str) -> str:
            if not built:
                built.append(summarizer_for(runtime.session.router))
            return await built[0](prompt)

        return summarize

    # ------------------------------------------------------------------ turn

    async def _turn(self, runtime: Runtime, *, max_iterations: int) -> AsyncIterator[Event]:
        """One ``run_turn``, with the checkpoint slipped in before the first edit."""
        self._checkpointed = False
        state = AgentState(
            messages=self.messages,
            cwd=str(runtime.loaded.paths.cwd),
            budget=self._elapsed_budget(),
            mode=runtime.loaded.mode,
        )
        model = self.model if self.model is not None else runtime.session.main
        final = state
        try:
            stream = run_turn(
                state,
                model,
                runtime.registry,
                runtime.policy,
                system=runtime.system,
                max_iterations=max_iterations,
            )
            async for event in stream:
                if isinstance(event, ToolStart) and self._mutating(runtime, event.name):
                    async for extra in self._checkpoint(runtime, event.name):
                        yield extra
                if isinstance(event, TurnStart):
                    self._turn_index = event.turn_index
                yield event
                if isinstance(event, ToolEnd) and event.result.ok:
                    self._mutated = self._mutated or self._mutating(runtime, event.name)
                elif isinstance(event, TurnEnd):
                    self._turn_index = event.turn_index
                    if event.agent_state is not None:
                        final = event.agent_state
        except StalledError as exc:
            # A stall is a failure of the run, not of a tool, and ``run_turn`` raises
            # instead of ending. It still carries a well-formed transcript, so the
            # session stays resumable — but the stream must still terminate the way
            # every consumer expects, with an Error and a TurnEnd.
            if exc.agent_state is not None:
                final = exc.agent_state
            self.notes.append(str(exc))
            yield Error(message=str(exc), kind="stalled", recoverable=True)
            yield TurnEnd(
                turn_index=self._turn_index,
                state=TurnState.ERROR,
                stop_reason="stalled",
                agent_state=exc.agent_state,
            )
        self.messages = final.messages
        self.budget = final.budget

    def _elapsed_budget(self) -> Budget:
        """The budget with wall time folded in at the turn boundary.

        The loop folds tokens and cost into ``Budget`` but never touches
        ``elapsed_seconds``, so ``max_wall_seconds`` is a ceiling nothing enforces
        unless someone advances it. The turn boundary is the only place with both a
        clock and the budget.
        """
        if self._started_at is None:
            return self.budget
        return replace(self.budget, elapsed_seconds=max(0.0, self.clock() - self._started_at))

    def _mutating(self, runtime: Runtime, name: str) -> bool:
        spec = runtime.registry.get(name)
        return spec is not None and spec.danger_level >= DangerLevel.MUTATING

    async def _checkpoint(self, runtime: Runtime, before: str) -> AsyncIterator[Event]:
        """Snapshot the work tree before the first mutating call of this turn.

        Called from inside the ``async for`` over ``run_turn``, which means
        ``run_turn`` is suspended at its ``yield ToolStart`` and the tool has not run.
        That is what makes this a checkpoint of the state the user was still happy
        with, rather than a snapshot of the damage.
        """
        if self._checkpointed:
            return
        self._checkpointed = True
        step = self._next_step(CHECKPOINT_STEP)
        yield ToolStart(tool_use_id=step, name=CHECKPOINT_STEP, arguments={"before": before})
        outcome = await runtime.checkpoints.checkpoint(f"before {before}")
        if outcome.ok and outcome.checkpoint is not None:
            self.checkpoints.append(outcome.checkpoint)
            self._turn_base = outcome.checkpoint.id
            content = (
                f"checkpoint {outcome.checkpoint.short} taken before {before}; "
                "/undo restores the work tree to it"
            )
            yield ToolEnd(
                tool_use_id=step,
                name=CHECKPOINT_STEP,
                result=ToolResult(ok=True, content=content),
            )
            return
        detail = _NO_CHECKPOINT.format(detail=outcome.detail)
        self.notes.append(detail)
        # ok=True on purpose: an absent checkpoint is a degradation the user must be
        # told about, not a failed action the model should try to recover from.
        yield ToolEnd(
            tool_use_id=step, name=CHECKPOINT_STEP, result=ToolResult(ok=True, content=detail)
        )

    # ---------------------------------------------------------------- verify

    async def _verify(self, runtime: Runtime, *, max_iterations: int) -> AsyncIterator[Event]:
        """Prove the turn's edits, or say honestly that nothing proved them."""
        root = runtime.loaded.paths.workspace_root
        changed = await self._changed_paths(runtime)
        if runtime.transcript is not None and changed:
            with contextlib.suppress(OSError, TranscriptError):
                runtime.transcript.note_files(changed)

        if not should_verify(mode=runtime.loaded.mode, changed_paths=changed):
            detail = _SKIPPED_BY_MODE.format(mode=runtime.loaded.mode.value)
            self.notes.append(detail)
            for event in self._verify_pair(changed, ToolResult(ok=True, content=detail)):
                yield event
            return

        plan = plan_for_changes(runtime.loaded.verify, changed, root=root)
        if plan.empty:
            detail = _NO_VERIFY_COMMANDS.format(count=len(changed))
            self.notes.append(detail)
            for event in self._verify_pair(changed, ToolResult(ok=True, content=detail)):
                yield event
            return

        async for event in self._repair(
            runtime, plan=plan, changed=changed, max_iterations=max_iterations
        ):
            yield event

    async def _changed_paths(self, runtime: Runtime) -> tuple[str, ...]:
        """What the turn actually changed, from the diff rather than from the model."""
        diff = await runtime.checkpoints.session_diff(base=self._turn_base)
        if not diff.ok:
            self.notes.append(_DIFF_UNAVAILABLE.format(detail=diff.detail))
            return ()
        return changed_paths_from_diff(diff.diff)

    def _verify_pair(self, changed: Sequence[str], result: ToolResult) -> tuple[Event, ...]:
        """The verify step as a tool pair, and as a tool couple in the transcript.

        Both, with the same id, so the event stream and the messages agree and a
        replayed session rebuilds exactly what the model was shown. The message pair
        is a ``ToolUse`` answered by a ``ToolResultBlock`` — never
        ``Message(role=Role.USER, ...)``. See :func:`ronin.verify.runner.as_tool_result`
        for why that distinction decides whether the model keeps working or re-scopes
        to explaining the traceback.
        """
        step = self._next_step(VERIFY_STEP)
        arguments = {"paths": list(changed)}
        self.messages = (
            *self.messages,
            Message(
                role=Role.ASSISTANT,
                content_blocks=(ToolUse(id=step, name=VERIFY_STEP, arguments=arguments),),
                metadata={"kind": VERIFY_STEP},
            ),
            Message(
                role=Role.TOOL,
                content_blocks=(result.as_block(step),),
                metadata={"kind": VERIFY_STEP},
            ),
        )
        return (
            ToolStart(tool_use_id=step, name=VERIFY_STEP, arguments=arguments),
            ToolEnd(tool_use_id=step, name=VERIFY_STEP, result=result),
        )

    async def _repair(
        self,
        runtime: Runtime,
        *,
        plan: VerifyPlan,
        changed: Sequence[str],
        max_iterations: int,
    ) -> AsyncIterator[Event]:
        """Run the plan, and repair under ``repair_loop``'s cap if it fails.

        ``repair_loop`` takes two callbacks, so the turns it drives cannot yield
        through it. It therefore runs as a task and its events arrive on a queue,
        which is drained here. The task is cancelled in the ``finally`` — a consumer
        that abandons this generator, or a user who interrupts, must not leave a
        turn running behind them.
        """
        root = runtime.loaded.paths.workspace_root
        queue: asyncio.Queue[Event | None] = asyncio.Queue()

        async def verify_step() -> FailureSnapshot:
            outcome = await run_plan(plan, run=self.command, cwd=root, timeout=self.verify_timeout)
            self.last_verify = outcome
            for event in self._verify_pair(changed, as_tool_result(outcome)):
                await queue.put(event)
            return FailureSnapshot.from_outcome(outcome)

        async def fix_step(snapshot: FailureSnapshot, index: int) -> str:
            del snapshot, index  # the failure already reached the model as a result
            base = self._turn_base
            async for event in self._turn(runtime, max_iterations=max_iterations):
                await queue.put(event)
            diff = await runtime.checkpoints.session_diff(base=base)
            return diff.diff if diff.ok else "(the diff for this attempt is unavailable)"

        async def driver() -> RepairReport:
            try:
                return await repair_loop(
                    verify=verify_step, fix=fix_step, max_attempts=self.max_repair_attempts
                )
            finally:
                await queue.put(None)

        report: RepairReport | None = None
        task = asyncio.create_task(driver())
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield item
            report = await task
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        self.last_repair = report
        if report is not None and not report.fixed:
            rendered = report.render()
            self.notes.append(rendered)
            # An Error, not a note: a run whose edits could not be verified did not
            # succeed, and `ronin -p` must exit non-zero however confident the
            # model's last paragraph was.
            yield Error(message=rendered, kind=VERIFY_STEP, recoverable=False)

    # ----------------------------------------------------------------- misc

    def _next_step(self, kind: str) -> str:
        """A unique id for one middleware step. Unique per conversation, not global."""
        self._step += 1
        return f"ronin-{kind}-{self._step}"


async def run_prompt(
    runtime: Runtime,
    prompt: str,
    *,
    budget: Budget | None = None,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    summarizer: Summarizer | None = None,
    verify: bool = True,
) -> AsyncIterator[Event]:
    """One prompt against ``runtime``, as an event stream. The seam in ``SEAMS.md``.

    A fresh :class:`Conversation` each call, which makes this the *one-shot* form —
    right for ``ronin -p`` and for the SDK's convenience path, and not enough for a
    multi-turn session, which needs the conversation to outlive one call. Hold a
    :class:`Conversation` and call its :meth:`~Conversation.run_prompt` for that; it
    is the same code, and this is a two-line wrapper over it.
    """
    conversation = Conversation()
    async for event in conversation.run_prompt(
        runtime,
        prompt,
        budget=budget,
        max_iterations=max_iterations,
        summarizer=summarizer,
        verify=verify,
    ):
        yield event


__all__ = [
    "CHECKPOINT_STEP",
    "COMPACT_STEP",
    "DEFAULT_MAX_ITERATIONS",
    "SUMMARY_MAX_TOKENS",
    "VERIFY_STEP",
    "CompactionPairingError",
    "Conversation",
    "plan_runtime",
    "run_prompt",
    "summarizer_for",
]
