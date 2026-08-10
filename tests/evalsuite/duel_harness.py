"""Scripted fakes for the duel tests. Offline, deterministic, no provider, no socket.

Named ``duel_harness`` rather than ``harness`` because the test trees are deliberately
not packages, so every module here is importable by its bare basename and
``tests/tools/harness.py`` already owns that name — see
``scripts/sync_typecheck_paths.py``, which fails loudly on the collision.

Two levels of fake live here, and the split is the point:

* :func:`fake_agent_factory` fakes only the **model**. Everything above it — the
  recording adapter, ``outcome_from_events``, ``judge``, ``build_report`` — is the real
  code, which is what makes :func:`fake_suite` an integration harness rather than a
  parallel implementation of the runner.
* :func:`report` and :func:`row` build report values directly, for the unit tests of
  pairing and scoreboard arithmetic where running anything would be noise.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from ronin.core.types import (
    AgentState,
    Budget,
    Event,
    TextDelta,
    ToolEnd,
    ToolResult,
    ToolStart,
    TurnEnd,
    TurnStart,
    TurnState,
)
from ronin.evals.adapters import OpenedAgent, RunAgent
from ronin.evals.duel import DuelistFactory, SuiteRunner
from ronin.evals.report import RunReport, TaskRow, build_report
from ronin.evals.task import EvalTask
from ronin.evals.taxonomy import AgentOutcome, FailureClass, RunRecord, VerifyProbe, judge

# --------------------------------------------------------------------------- #
# Tasks
# --------------------------------------------------------------------------- #


def task(
    task_id: str,
    *,
    root: Path | None = None,
    category: str = "edit",
    prompt: str = "do the thing",
    files_expected: tuple[str, ...] = (),
    max_turns: int = 8,
) -> EvalTask:
    return EvalTask(
        id=task_id,
        category=category,
        prompt=prompt,
        root=root if root is not None else Path("."),
        files_expected=files_expected,
        max_turns=max_turns,
    )


# --------------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Call:
    """One scripted tool call inside a fake turn."""

    id: str
    name: str
    arguments: Mapping[str, object] = field(default_factory=dict)
    ok: bool = True
    body: str = "done"
    #: What the tool reports it touched. ``adapters.outcome_from_events`` derives
    #: ``edited_paths`` from a successful edit tool's ``ToolResult.artifacts``, not from
    #: its arguments, so a fixture that only sets ``arguments`` records no edits at all.
    artifacts: tuple[str, ...] = ()


def turn(
    index: int,
    *,
    text: str = "",
    thinking: str = "",
    calls: Sequence[Call] = (),
    state: TurnState = TurnState.DONE,
    stop_reason: str = "complete",
) -> tuple[Event, ...]:
    """One plausible turn as an event tuple.

    Text arrives as several ``TextDelta`` events rather than one, because coalescing
    those is exactly what ``transcript_lines`` claims to do and a single-delta fixture
    would never exercise it.
    """
    events: list[Event] = [TurnStart(turn_index=index)]
    if thinking:
        events.extend(TextDelta(text=piece, thinking=True) for piece in _split(thinking))
    if text:
        events.extend(TextDelta(text=piece) for piece in _split(text))
    for call in calls:
        events.append(
            ToolStart(tool_use_id=call.id, name=call.name, arguments=dict(call.arguments))
        )
        result = (
            ToolResult(ok=True, content=call.body, artifacts=call.artifacts)
            if call.ok
            else ToolResult(ok=False, error=call.body, artifacts=call.artifacts)
        )
        events.append(ToolEnd(tool_use_id=call.id, name=call.name, result=result))
    events.append(TurnEnd(turn_index=index, state=state, stop_reason=stop_reason))
    return tuple(events)


def _split(text: str, size: int = 4) -> tuple[str, ...]:
    return tuple(text[i : i + size] for i in range(0, len(text), size)) or ("",)


# --------------------------------------------------------------------------- #
# The model seam
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class FakeRun:
    """Stands in for ``ronin.cli.sdk.AgentResult``."""

    text: str = "done"
    exit_code: int = 0
    events: tuple[Event, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(slots=True)
class FakeAgent:
    """Stands in for ``ronin.cli.sdk.Agent``. Mutable so ``closed`` can be observed."""

    run_result: FakeRun
    budget: Budget = field(default_factory=Budget)
    raises: str = ""
    closed: bool = False

    @property
    def state(self) -> AgentState:
        return AgentState(budget=self.budget)

    async def run(self, prompt: str, *, max_iterations: int = 0) -> FakeRun:
        if self.raises:
            raise RuntimeError(self.raises)
        return self.run_result

    async def aclose(self) -> None:
        self.closed = True


#: ``(duelist, seed, task) -> FakeRun``. A function rather than a table so a side's
#: behaviour can depend on the seed, which is how "a different seed is allowed to
#: differ" is expressed without special-casing anything in the module under test.
RunScript = Callable[[str, int, EvalTask], FakeRun]


def fake_agent_factory(
    duelist: str,
    seed: int,
    script: RunScript,
    *,
    opened: list[FakeAgent] | None = None,
    tools: tuple[str, ...] = ("read", "edit", "bash"),
    budget: Budget | None = None,
) -> Callable[[EvalTask, Path], object]:
    """An ``AgentFactory`` for one duelist at one seed."""

    async def open_agent(eval_task: EvalTask, workspace: Path) -> OpenedAgent:
        agent = FakeAgent(
            run_result=script(duelist, seed, eval_task),
            budget=budget if budget is not None else Budget(spent_tokens=120, spent_usd=0.02),
        )
        if opened is not None:
            opened.append(agent)
        return OpenedAgent(agent=agent, registered_tools=tools)

    return open_agent


def duelist_factory(
    script: RunScript,
    *,
    seen: list[tuple[str, int]] | None = None,
    opened: list[FakeAgent] | None = None,
) -> DuelistFactory:
    """A :data:`ronin.evals.duel.DuelistFactory` over ``script``.

    ``seen`` collects every ``(duelist, seed)`` the duel asked for — how the tests
    check that the seed reaches the factory and that both sides receive the same one.
    """

    def factory(duelist: str, seed: int) -> object:
        if seen is not None:
            seen.append((duelist, seed))
        return fake_agent_factory(duelist, seed, script, opened=opened)

    return factory  # type: ignore[return-value]  # structural AgentFactory


# --------------------------------------------------------------------------- #
# A suite runner that is real above the model
# --------------------------------------------------------------------------- #

#: ``(task, outcome) -> (verify_exit_code, timed_out)``. The suite's gate, scripted.
Gate = Callable[[EvalTask, AgentOutcome], tuple[int, bool]]


def fake_suite(
    root: Path,
    gate: Gate,
    *,
    walls: Sequence[float] = (),
) -> SuiteRunner:
    """A :data:`ronin.evals.duel.SuiteRunner` that skips workspace materialization only.

    Everything that shapes the comparison is the real thing: the ``RunAgent`` handed in
    is invoked, ``judge`` classifies, and ``build_report`` folds. What is faked is the
    filesystem setup and ``verify.sh`` — a real one would need a task tree on disk and a
    subprocess, and neither changes what a duel does with the result.
    """
    clock = iter(walls)

    async def run(tasks: Sequence[EvalTask], agent: RunAgent, label: str) -> RunReport:
        results = []
        for eval_task in tasks:
            workspace = root / label / eval_task.id
            workspace.mkdir(parents=True, exist_ok=True)
            outcome = await agent(eval_task, workspace)
            exit_code, timed_out = gate(eval_task, outcome)
            record = RunRecord(
                task_id=eval_task.id,
                category=eval_task.category,
                prompt=eval_task.prompt,
                files_expected=eval_task.files_expected,
                outcome=outcome,
                verify_after=VerifyProbe(ran=True, exit_code=exit_code),
                wall_seconds=next(clock, 1.0),
                timed_out=timed_out,
            )
            results.append(judge(record))
        return build_report(results, label=label, model=label)

    return run


# --------------------------------------------------------------------------- #
# Report values, for the unit tests of pairing and arithmetic
# --------------------------------------------------------------------------- #


def row(
    task_id: str,
    *,
    status: str = "pass",
    category: str = "edit",
    turns: int = 2,
    tokens: str = "120",
    cost: str = "$0.0200",
    wall_seconds: float = 1.0,
    classes: tuple[FailureClass, ...] = (),
) -> TaskRow:
    return TaskRow(
        task_id=task_id,
        category=category,
        status=status,
        turns=turns,
        tokens=tokens,
        cost=cost,
        wall_seconds=wall_seconds,
        classes=classes,
    )


def report(label: str, *rows: TaskRow) -> RunReport:
    return RunReport(label=label, model=label, rows=rows)


def slower(source: RunReport, factor: float) -> RunReport:
    """The same report with every wall-clock reading multiplied. Nothing else moves.

    Used to prove the scoreboard ignores wall time: if the two render identically, the
    determinism claim is not resting on a fast machine.
    """
    return replace(
        source,
        rows=tuple(
            replace(entry, wall_seconds=entry.wall_seconds * factor) for entry in source.rows
        ),
    )


__all__ = [
    "Call",
    "FakeAgent",
    "FakeRun",
    "Gate",
    "RunScript",
    "duelist_factory",
    "fake_agent_factory",
    "fake_suite",
    "report",
    "row",
    "slower",
    "task",
    "turn",
]
