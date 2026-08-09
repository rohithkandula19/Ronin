"""Scripted fakes for the duel tests. Offline, deterministic, no provider.

Named ``duel_harness`` rather than ``harness`` because the test trees are not
packages, so every module here is importable by its bare basename and
``tests/tools/harness.py`` already owns that name (see
``scripts/sync_typecheck_paths.py``).

The fakes here stand in for two things agent B owns: an ``EvalTask``
(:class:`ScriptedTask`) and a ``RunRecord`` (:class:`FakeRecord`). They are
deliberately *structural* stand-ins — they satisfy ``ronin.evals.duel.TaskLike`` and
``RecordLike`` by shape, which is the same way the real ones will.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ronin.core.types import (
    Event,
    TextDelta,
    ToolEnd,
    ToolResult,
    ToolStart,
    TurnEnd,
    TurnStart,
    TurnState,
)
from ronin.evals.duel import DuelistFactory, RecordLike, RunAgent, Side, TaskLike


@dataclass(frozen=True, slots=True)
class ScriptedTask:
    """Stands in for ``ronin.evals.task.EvalTask``: an id and something to run."""

    id: str
    prompt: str = "do the thing"


@dataclass(frozen=True, slots=True)
class FakeRecord:
    """Stands in for the eval runner's ``RunRecord``.

    Carries the seven names ``ronin.evals.duel.RecordLike`` reads and nothing else,
    so a test that passes here is a test of the protocol rather than of this class.
    """

    passed: bool = False
    turns: int = 1
    tokens: int = 100
    cost_usd: float = 0.01
    wall_seconds: float = 1.0
    taxonomy: tuple[str, ...] = ()
    events: tuple[Event, ...] = ()


@dataclass(frozen=True, slots=True)
class Call:
    """One scripted tool call inside a fake turn."""

    id: str
    name: str
    arguments: Mapping[str, object] = field(default_factory=dict)
    ok: bool = True
    body: str = "done"


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
            ToolResult(ok=True, content=call.body)
            if call.ok
            else ToolResult(ok=False, error=call.body)
        )
        events.append(ToolEnd(tool_use_id=call.id, name=call.name, result=result))
    events.append(TurnEnd(turn_index=index, state=state, stop_reason=stop_reason))
    return tuple(events)


def _split(text: str, size: int = 4) -> tuple[str, ...]:
    return tuple(text[i : i + size] for i in range(0, len(text), size)) or ("",)


#: ``(duelist, task_id, seed) -> record``. A function rather than a table so a test
#: can make a side's behaviour depend on the seed, which is how "different seed is
#: allowed to differ" is expressed without special-casing anything in the module.
RecordScript = Callable[[str, str, int], FakeRecord]


def table_script(table: Mapping[tuple[str, str], FakeRecord]) -> RecordScript:
    """A seed-independent script keyed by ``(duelist, task_id)``."""

    def script(duelist: str, task_id: str, seed: int) -> FakeRecord:
        try:
            return table[(duelist, task_id)]
        except KeyError as exc:  # a missing entry is a broken test, not a fail result
            raise AssertionError(f"no scripted record for {duelist!r} on {task_id!r}") from exc

    return script


def scripted_factory(
    script: RecordScript, *, seen: list[tuple[str, int]] | None = None
) -> DuelistFactory:
    """A :data:`ronin.evals.duel.DuelistFactory` over ``script``.

    ``seen`` collects every ``(duelist, seed)`` the duel asked for, which is how the
    tests check that the seed actually reaches the factory and that both sides get
    the same one.
    """

    def factory(duelist: str, seed: int) -> RunAgent:
        if seen is not None:
            seen.append((duelist, seed))

        async def run(task: TaskLike, workdir: Path) -> RecordLike:
            return script(duelist, task.id, seed)

        return run

    return factory


def workdirs(root: Path) -> Callable[[TaskLike, Side], Path]:
    """A ``WorkdirFactory`` under ``root``; each side gets its own directory."""

    def make(task: TaskLike, side: Side) -> Path:
        path = root / task.id / side.value
        path.mkdir(parents=True, exist_ok=True)
        return path

    return make


__all__ = [
    "Call",
    "FakeRecord",
    "RecordScript",
    "ScriptedTask",
    "scripted_factory",
    "table_script",
    "turn",
    "workdirs",
]
