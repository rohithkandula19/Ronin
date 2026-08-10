"""Shared fakes for the eval-suite tests. Offline, deterministic, no providers.

Named ``evals_harness`` rather than ``harness`` because the test trees are not
packages: every module under ``tests/*/`` is importable by bare basename, and
``harness`` is already taken by ``tests/tools``.

Everything here exists to keep a test's *subject* visible. A taxonomy test should read
"given an edit that failed with an ambiguity error, the class is EDIT_AMBIGUITY" — not
twenty lines of dataclass construction.
"""

from __future__ import annotations

import textwrap
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ronin.core.types import (
    AgentState,
    Budget,
    Error,
    Event,
    ToolEnd,
    ToolResult,
    ToolStart,
    TurnEnd,
    TurnStart,
    TurnState,
)
from ronin.evals.adapters import OpenedAgent
from ronin.evals.task import EvalTask
from ronin.evals.taxonomy import (
    AgentOutcome,
    RunRecord,
    ToolCallRecord,
    VerifyProbe,
)
from ronin.verify.runner import CommandOutcome

DEFAULT_TOOLS: tuple[str, ...] = (
    "bash",
    "edit",
    "glob",
    "grep",
    "ls",
    "multi_edit",
    "read",
    "write",
)


# --------------------------------------------------------------------------- #
# Fixtures on disk
# --------------------------------------------------------------------------- #


def write_task(
    suite_root: Path,
    task_id: str,
    *,
    category: str = "edit",
    prompt: str = "fix the bug",
    fixture: Mapping[str, str] | None = None,
    solution: Mapping[str, str] | None = None,
    verify: str | None = None,
    verify_inside_fixture: bool = False,
    extra_toml: str = "",
    files_expected: Sequence[str] = (),
    fixture_dirname: str | None = None,
) -> Path:
    """Write one task directory under ``suite_root`` and return it.

    ``solution`` lands in a sibling ``solution/`` exactly as the real fixtures do, so
    a test can assert it never reaches a workspace.
    """
    root = suite_root / category / task_id
    root.mkdir(parents=True, exist_ok=True)

    expected = ", ".join(f'"{path}"' for path in files_expected)
    fixture_line = f'fixture = "{fixture_dirname}"\n' if fixture_dirname else ""
    (root / "task.toml").write_text(
        textwrap.dedent(
            f"""\
            id = "{task_id}"
            category = "{category}"
            prompt = "{prompt}"
            files_expected = [{expected}]
            """
        )
        + fixture_line
        + extra_toml,
        encoding="utf-8",
    )

    if fixture is not None:
        base = root / (fixture_dirname or "fixture")
        for name, content in fixture.items():
            target = base / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
    if solution is not None:
        for name, content in solution.items():
            target = root / "solution" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
    if verify is not None:
        where = (root / (fixture_dirname or "fixture")) if verify_inside_fixture else root
        where.mkdir(parents=True, exist_ok=True)
        (where / "verify.sh").write_text(verify, encoding="utf-8")
    return root


def write_manifest(suite_root: Path, task_ids: Sequence[str], *, key: str = "tasks") -> Path:
    """Write ``manifest.toml`` listing ``task_ids``."""
    suite_root.mkdir(parents=True, exist_ok=True)
    body = ", ".join(f'"{name}"' for name in task_ids)
    path = suite_root / "manifest.toml"
    path.write_text(f"{key} = [{body}]\n", encoding="utf-8")
    return path


def task(
    task_id: str = "t1",
    *,
    category: str = "edit",
    prompt: str = "fix it",
    root: Path | None = None,
    **kwargs: Any,
) -> EvalTask:
    """An :class:`EvalTask` with no directory behind it, for pure-function tests."""
    return EvalTask(
        id=task_id,
        category=category,
        prompt=prompt,
        root=root if root is not None else Path("."),
        **kwargs,
    )


# --------------------------------------------------------------------------- #
# A scripted agent
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Step:
    """One tool call the scripted agent makes.

    ``content`` is written into the workspace when the step succeeds, so a scripted
    run genuinely changes the tree the runner then verifies — a fake that only emits
    events could not exercise ``BROKE_TESTS`` at all.
    """

    tool: str = "edit"
    path: str = ""
    content: str | None = None
    ok: bool = True
    error: str = ""
    result: str = ""
    arguments: Mapping[str, Any] | None = None

    def tool_arguments(self) -> Mapping[str, Any]:
        if self.arguments is not None:
            return self.arguments
        return {"path": self.path} if self.path else {}


@dataclass
class ScriptedRun:
    """Stands in for ``ronin.cli.sdk.AgentResult``."""

    text: str = ""
    exit_code: int = 0
    events: tuple[Event, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass
class ScriptedAgent:
    """A fake agent: performs its script's writes, then reports the matching events.

    Mutable because it records what it was asked (``prompts``), which is how a test
    asserts the runner handed it the right task.
    """

    steps: Sequence[Step] = ()
    final_text: str = "done"
    workspace: Path = field(default_factory=Path)
    budget: Budget = field(default_factory=Budget)
    stalled: bool = False
    stop_reason: str = "no_tool_calls"
    raises: BaseException | None = None
    sleep_forever: bool = False
    prompts: list[str] = field(default_factory=list)
    closed: bool = False

    @property
    def state(self) -> AgentState:
        return AgentState(budget=self.budget)

    async def run(self, prompt: str, *, max_iterations: int = 25) -> ScriptedRun:
        self.prompts.append(prompt)
        if self.raises is not None:
            raise self.raises
        if self.sleep_forever:
            import asyncio

            await asyncio.sleep(3600)
        events: list[Event] = []
        for index, step in enumerate(self.steps):
            events.append(TurnStart(turn_index=index))
            use_id = f"call-{index}"
            events.append(
                ToolStart(tool_use_id=use_id, name=step.tool, arguments=step.tool_arguments())
            )
            artifacts: tuple[str, ...] = ()
            if step.ok and step.path:
                target = self.workspace / step.path
                if step.content is not None:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(step.content, encoding="utf-8")
                artifacts = (str(target),)
            result = (
                ToolResult(ok=True, content=step.result or "ok", artifacts=artifacts)
                if step.ok
                else ToolResult(ok=False, error=step.error or "failed")
            )
            events.append(ToolEnd(tool_use_id=use_id, name=step.tool, result=result))
        if self.stalled:
            events.append(Error(message="stalled: repeated", kind="stalled", recoverable=True))
            events.append(
                TurnEnd(
                    turn_index=max(0, len(self.steps) - 1),
                    state=TurnState.ERROR,
                    stop_reason="stalled",
                )
            )
        else:
            events.append(
                TurnEnd(
                    turn_index=max(0, len(self.steps) - 1),
                    state=TurnState.DONE,
                    stop_reason=self.stop_reason,
                )
            )
        return ScriptedRun(text=self.final_text, events=tuple(events))

    async def aclose(self) -> None:
        self.closed = True


def scripted_factory(
    steps: Sequence[Step] = (),
    *,
    final_text: str = "done",
    budget: Budget | None = None,
    stalled: bool = False,
    raises: BaseException | None = None,
    sleep_forever: bool = False,
    registered_tools: Sequence[str] = DEFAULT_TOOLS,
    opened: list[ScriptedAgent] | None = None,
) -> Callable[[EvalTask, Path], Awaitable[OpenedAgent]]:
    """An ``AgentFactory`` yielding a :class:`ScriptedAgent` per task.

    ``opened`` collects the agents so a test can assert every one was closed.
    """

    async def factory(eval_task: EvalTask, workspace: Path) -> OpenedAgent:
        agent = ScriptedAgent(
            steps=steps,
            final_text=final_text,
            workspace=workspace,
            budget=budget if budget is not None else Budget(),
            stalled=stalled,
            raises=raises,
            sleep_forever=sleep_forever,
        )
        if opened is not None:
            opened.append(agent)
        return OpenedAgent(agent=agent, registered_tools=tuple(registered_tools))

    return factory


# --------------------------------------------------------------------------- #
# A scripted verify.sh
# --------------------------------------------------------------------------- #

CommandScript = Callable[..., Awaitable[CommandOutcome]]


def verify_runner(
    *,
    baseline: tuple[int, str] = (1, ""),
    work: tuple[int, str] = (0, ""),
    duration_s: float = 0.0,
) -> CommandScript:
    """A ``CommandRunner`` answering by which copy it was pointed at.

    The runner materializes the fixture twice — ``baseline/`` for the pre-run probe
    and ``work/`` for the agent — so keying on the directory name is exactly the
    distinction the ``BROKE_TESTS`` baseline depends on.
    """

    async def run(argv: Sequence[str], *, cwd: Path, **_: object) -> CommandOutcome:
        exit_code, output = baseline if Path(cwd).name == "baseline" else work
        return CommandOutcome(
            argv=tuple(argv), exit_code=exit_code, output=output, duration_s=duration_s
        )

    return run


def frozen_clock(value: float = 0.0) -> Callable[[], float]:
    """A clock that never moves, so wall-time fields are byte-identical across runs."""

    def clock() -> float:
        return value

    return clock


def stepping_clock(step: float = 1.0) -> Callable[[], float]:
    """A clock advancing by ``step`` per call. Deterministic only when serial."""
    state = {"now": 0.0}

    def clock() -> float:
        state["now"] += step
        return state["now"]

    return clock


# --------------------------------------------------------------------------- #
# Evidence builders for the taxonomy
# --------------------------------------------------------------------------- #


def call(
    name: str = "edit",
    *,
    ok: bool = True,
    error: str = "",
    content: str = "",
    paths: Sequence[str] = (),
    mark: str = "",
) -> ToolCallRecord:
    return ToolCallRecord(
        name=name,
        fingerprint=mark or f"{name}:{{}}",
        ok=ok,
        error=error,
        content=content,
        paths=tuple(paths),
    )


def probe(exit_code: int = 0, output: str = "", *, ran: bool = True) -> VerifyProbe:
    return VerifyProbe(ran=ran, exit_code=exit_code, output=output)


def record(
    *,
    task_id: str = "t1",
    category: str = "edit",
    files_expected: Sequence[str] = (),
    tool_calls: Sequence[ToolCallRecord] = (),
    edited_paths: Sequence[str] = (),
    final_text: str = "",
    stalled: bool = False,
    stop_reason: str = "",
    registered_tools: Sequence[str] = DEFAULT_TOOLS,
    turns_used: int = 1,
    outcome_error: str = "",
    verify_before: VerifyProbe | None = None,
    verify_after: VerifyProbe | None = None,
    known_symbols: Sequence[str] = (),
    wall_seconds: float = 0.0,
    timed_out: bool = False,
    skipped: bool = False,
    skip_reason: str = "",
    error: str = "",
) -> RunRecord:
    """A :class:`RunRecord` from keyword evidence. Defaults describe a failing run."""
    return RunRecord(
        task_id=task_id,
        category=category,
        files_expected=tuple(files_expected),
        outcome=AgentOutcome(
            turns_used=turns_used,
            tool_calls=tuple(tool_calls),
            edited_paths=tuple(edited_paths),
            final_text=final_text,
            stalled=stalled,
            stop_reason=stop_reason,
            registered_tools=tuple(registered_tools),
            error=outcome_error,
        ),
        verify_before=verify_before if verify_before is not None else probe(1),
        verify_after=verify_after if verify_after is not None else probe(1),
        known_symbols=frozenset(known_symbols),
        wall_seconds=wall_seconds,
        timed_out=timed_out,
        skipped=skipped,
        skip_reason=skip_reason,
        error=error,
    )


PASSING_VERIFY = "#!/usr/bin/env bash\nexit 0\n"
FAILING_VERIFY = "#!/usr/bin/env bash\necho 'FAILED tests/test_x.py::test_one'\nexit 1\n"

#: A ``verify.sh`` that passes only once the fixture's bug is fixed. Uses ``grep``
#: rather than python so it works on any machine with bash.
CONDITIONAL_VERIFY = (
    "#!/usr/bin/env bash\n"
    "if grep -q 'return a + b' app.py; then exit 0; fi\n"
    "echo 'FAILED tests/test_app.py::test_add'\n"
    "exit 1\n"
)
