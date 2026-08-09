"""The runner's guarantees: bounded, isolated, timed out, unkillable, deterministic."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from evals_harness import (
    CONDITIONAL_VERIFY,
    FAILING_VERIFY,
    PASSING_VERIFY,
    Step,
    frozen_clock,
    scripted_factory,
    verify_runner,
    write_manifest,
    write_task,
)

from ronin.core.types import Budget
from ronin.evals.adapters import RunAgent, v2_adapter
from ronin.evals.report import report_json
from ronin.evals.runner import EvalRunner, RunnerConfig, SuiteSelection, run_suite
from ronin.evals.task import EvalTask, load_suite
from ronin.evals.taxonomy import AgentOutcome, FailureClass

BROKEN_APP = "def add(a, b):\n    return a - b\n"
FIXED_APP = "def add(a, b):\n    return a + b\n"


def suite_of(tmp_path: Path, count: int = 3, *, verify: str = CONDITIONAL_VERIFY) -> list[EvalTask]:
    root = tmp_path / "suite"
    ids = [f"task-{index}" for index in range(count)]
    for name in ids:
        write_task(
            root,
            name,
            fixture={"app.py": BROKEN_APP},
            solution={"app.py": FIXED_APP},
            verify=verify,
            files_expected=["app.py"],
        )
    write_manifest(root, ids)
    return list(load_suite(root))


# --------------------------------------------------------------------------- #
# The happy path, with a real bash verify.sh
# --------------------------------------------------------------------------- #


async def test_an_agent_that_fixes_the_fixture_passes(tmp_path: Path) -> None:
    tasks = suite_of(tmp_path, 1)
    adapter = v2_adapter(scripted_factory([Step(tool="edit", path="app.py", content=FIXED_APP)]))
    report = await run_suite(tasks, adapter, config=RunnerConfig(label="fake"))
    assert report.overall.passed == 1
    assert report.overall.pass_rate == 1.0
    assert report.rows[0].status == "pass"
    assert report.rows[0].classes == ()


async def test_an_agent_that_edits_the_wrong_file_fails_as_wrong_file(tmp_path: Path) -> None:
    tasks = suite_of(tmp_path, 1)
    adapter = v2_adapter(
        scripted_factory([Step(tool="edit", path="notes.md", content="hello\n")])
    )
    report = await run_suite(tasks, adapter)
    assert report.overall.passed == 0
    assert FailureClass.WRONG_FILE in report.rows[0].classes


async def test_the_agent_never_sees_the_solution_directory(tmp_path: Path) -> None:
    """The one leak that would make every number in every report meaningless."""
    tasks = suite_of(tmp_path, 1)
    seen: list[list[str]] = []

    async def peek(task: EvalTask, workspace: Path) -> AgentOutcome:
        seen.append(sorted(path.name for path in workspace.rglob("*")))
        return AgentOutcome()

    await run_suite(tasks, peek)
    assert seen
    assert "solution" not in seen[0]
    assert "app.py" in seen[0]


# --------------------------------------------------------------------------- #
# Baseline / BROKE_TESTS
# --------------------------------------------------------------------------- #


async def test_a_regression_against_the_pre_run_baseline_is_broke_tests(tmp_path: Path) -> None:
    tasks = suite_of(tmp_path, 1, verify=PASSING_VERIFY)
    # The fixture verifies clean; the agent replaces verify.sh with a failing one.
    adapter = v2_adapter(
        scripted_factory([Step(tool="write", path="verify.sh", content=FAILING_VERIFY)])
    )
    report = await run_suite(tasks, adapter)
    assert FailureClass.BROKE_TESTS in report.rows[0].classes


async def test_the_baseline_probe_runs_on_an_untouched_copy(tmp_path: Path) -> None:
    """If the baseline saw the agent's edits, no regression could ever be detected."""
    tasks = suite_of(tmp_path, 1)
    probed: list[str] = []

    async def spy(argv: list[str], *, cwd: Path, **_: object) -> object:
        probed.append(Path(cwd).name)
        from ronin.verify.runner import CommandOutcome

        contents = (Path(cwd) / "app.py").read_text(encoding="utf-8")
        return CommandOutcome(argv=tuple(argv), exit_code=0 if "a + b" in contents else 1)

    adapter = v2_adapter(scripted_factory([Step(tool="edit", path="app.py", content=FIXED_APP)]))
    runner = EvalRunner(adapter, command_runner=spy)  # type: ignore[arg-type]
    report = await runner.run(tasks)
    assert probed == ["baseline", "work"]
    assert report.results[0].record.verify_before.passed is False
    assert report.results[0].record.verify_after.passed is True


# --------------------------------------------------------------------------- #
# Concurrency
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("parallel", [1, 2, 4])
async def test_parallelism_is_actually_bounded(tmp_path: Path, parallel: int) -> None:
    tasks = suite_of(tmp_path, 8)
    live = 0
    peak = 0

    async def slow(task: EvalTask, workspace: Path) -> AgentOutcome:
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        try:
            # Two hops through the event loop, so overlap is possible if the cap
            # is not real. A sleep(0) can be optimised into no suspension at all.
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            return AgentOutcome()
        finally:
            live -= 1

    runner = EvalRunner(
        slow, config=RunnerConfig(parallel=parallel), command_runner=verify_runner()
    )
    await runner.run(tasks)
    assert peak <= parallel
    assert runner.peak_concurrency <= parallel
    assert len(runner.results) == 8


async def test_a_higher_cap_actually_overlaps(tmp_path: Path) -> None:
    """The bound test above passes vacuously if nothing ever runs concurrently."""
    tasks = suite_of(tmp_path, 6)
    gate = asyncio.Event()
    arrived = 0

    async def wait_for_three(task: EvalTask, workspace: Path) -> AgentOutcome:
        nonlocal arrived
        arrived += 1
        if arrived >= 3:
            gate.set()
        await gate.wait()
        return AgentOutcome()

    runner = EvalRunner(
        wait_for_three, config=RunnerConfig(parallel=3), command_runner=verify_runner()
    )
    await asyncio.wait_for(runner.run(tasks), timeout=10)
    assert runner.peak_concurrency == 3


# --------------------------------------------------------------------------- #
# Failure containment
# --------------------------------------------------------------------------- #


async def test_a_task_that_times_out_yields_a_failed_result_not_a_hang(tmp_path: Path) -> None:
    root = tmp_path / "suite"
    write_task(
        root,
        "slow",
        fixture={"app.py": BROKEN_APP},
        verify=CONDITIONAL_VERIFY,
        extra_toml="timeout_seconds = 0.05\n",
    )
    write_manifest(root, ["slow"])
    tasks = list(load_suite(root))
    adapter = v2_adapter(scripted_factory(sleep_forever=True))
    report = await asyncio.wait_for(run_suite(tasks, adapter), timeout=10)
    assert report.rows[0].status == "timeout"
    assert report.overall.timed_out == 1
    assert "timed out" in report.results[0].record.error


async def test_a_task_that_raises_does_not_kill_the_run(tmp_path: Path) -> None:
    tasks = suite_of(tmp_path, 3)

    async def explode(task: EvalTask, workspace: Path) -> AgentOutcome:
        if task.id == "task-1":
            raise RuntimeError("fixture is cursed")
        return AgentOutcome()

    report = await run_suite(tasks, explode, config=RunnerConfig(parallel=3))
    assert len(report.rows) == 3
    failed = next(row for row in report.rows if row.task_id == "task-1")
    assert failed.status == "error"
    assert "fixture is cursed" in failed.detail
    assert report.overall.errored == 1


async def test_an_adapter_that_raises_inside_the_agent_becomes_a_recorded_error(
    tmp_path: Path,
) -> None:
    """The v2 adapter converts the escape itself, so the agent is still closed."""
    tasks = suite_of(tmp_path, 1)
    opened: list[object] = []
    adapter = v2_adapter(
        scripted_factory(raises=RuntimeError("no credentials"), opened=opened)  # type: ignore[arg-type]
    )
    report = await run_suite(tasks, adapter)
    assert report.rows[0].status == "error"
    assert "no credentials" in report.rows[0].detail
    assert all(getattr(agent, "closed", False) for agent in opened)


async def test_a_git_task_is_skipped_offline_with_the_reason_recorded(tmp_path: Path) -> None:
    root = tmp_path / "suite"
    write_task(
        root,
        "swe-1",
        category="repo",
        extra_toml='git_sha = "deadbeef"\ngit_url = "https://example.invalid/r.git"\n',
    )
    write_manifest(root, ["swe-1"])
    tasks = list(load_suite(root))
    report = await run_suite(tasks, _never_called)
    assert report.rows[0].status == "skip"
    assert report.overall.skipped == 1
    assert report.overall.attempted == 0
    assert report.overall.pass_rate is None  # never 0.0 over an all-skipped suite
    assert "needs network" in report.rows[0].detail
    assert report.rows[0].classes == ()


async def _never_called(task: EvalTask, workspace: Path) -> AgentOutcome:
    raise AssertionError("a skipped task must not reach the agent")


async def test_a_cancelled_run_leaves_well_formed_results_and_does_not_hang(
    tmp_path: Path,
) -> None:
    tasks = suite_of(tmp_path, 4)
    started = asyncio.Event()

    async def hang(task: EvalTask, workspace: Path) -> AgentOutcome:
        started.set()
        await asyncio.sleep(3600)
        return AgentOutcome()

    runner = EvalRunner(hang, config=RunnerConfig(parallel=4), command_runner=verify_runner())
    job = asyncio.create_task(runner.run(tasks))
    await asyncio.wait_for(started.wait(), timeout=5)
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(job, timeout=10)
    assert len(runner.results) == 4
    assert {result.record.error for result in runner.results} == {"cancelled"}
    # A report is still buildable, which is the point of keeping partials.
    assert runner.report().overall.tasks == 4


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #


async def test_two_seeded_runs_produce_byte_identical_reports(tmp_path: Path) -> None:
    tasks = suite_of(tmp_path, 6)
    steps = [
        Step(tool="read", path="app.py"),
        Step(tool="edit", path="app.py", content=FIXED_APP),
    ]

    async def one() -> bytes:
        adapter = v2_adapter(scripted_factory(steps, budget=Budget(spent_tokens=1234)))
        report = await run_suite(
            tasks,
            adapter,
            config=RunnerConfig(parallel=4, label="fake", model="fake-1"),
            clock=frozen_clock(),
        )
        return report_json(report).encode("utf-8")

    assert await one() == await one()


async def test_the_report_never_carries_the_temp_workspace_path(tmp_path: Path) -> None:
    """A temp path in the machine record is what makes two identical runs differ."""
    tasks = suite_of(tmp_path, 1)
    adapter = v2_adapter(scripted_factory([Step(tool="edit", path="app.py", content=FIXED_APP)]))
    runner = EvalRunner(adapter, clock=frozen_clock())
    report = await runner.run(tasks)
    assert "ronin-eval-" not in report_json(report)
    assert report.results[0].record.workspace  # still recorded on the record itself


# --------------------------------------------------------------------------- #
# Usage
# --------------------------------------------------------------------------- #


async def test_a_provider_that_reports_no_usage_is_recorded_as_unreported(
    tmp_path: Path,
) -> None:
    tasks = suite_of(tmp_path, 1)
    adapter = v2_adapter(scripted_factory([Step(tool="read", path="app.py")], budget=Budget()))
    report = await run_suite(tasks, adapter)
    usage = report.results[0].record.usage
    assert usage.reported is False
    assert report.rows[0].tokens == "unreported"
    assert report.rows[0].cost == "unreported"
    assert report.distributions["tokens"].count == 0
    assert report.distributions["tokens"].absent == 1


async def test_reported_usage_is_carried_through_to_the_distributions(tmp_path: Path) -> None:
    tasks = suite_of(tmp_path, 2)
    adapter = v2_adapter(
        scripted_factory(
            [Step(tool="read", path="app.py")], budget=Budget(spent_tokens=500, spent_usd=0.02)
        )
    )
    report = await run_suite(tasks, adapter)
    assert report.rows[0].tokens == "500"
    assert report.rows[0].cost == "$0.0200"
    assert report.distributions["tokens"].count == 2
    assert report.distributions["tokens"].total == 1000.0
    assert report.distributions["cost_usd"].count == 2


# --------------------------------------------------------------------------- #
# Workspaces, selection
# --------------------------------------------------------------------------- #


async def test_workspaces_are_removed_unless_asked_for(tmp_path: Path) -> None:
    tasks = suite_of(tmp_path, 1)
    keep = tmp_path / "kept"
    adapter: RunAgent = v2_adapter(scripted_factory([Step(tool="read", path="app.py")]))
    await run_suite(tasks, adapter, config=RunnerConfig(workspace_root=keep))
    assert list(keep.iterdir()) == []

    await run_suite(
        tasks, adapter, config=RunnerConfig(workspace_root=keep, keep_workspaces=True)
    )
    kept_dirs = sorted(path.name for path in keep.iterdir())
    assert len(kept_dirs) == 1 and kept_dirs[0].startswith("task-0-")
    work = keep / kept_dirs[0] / "work"
    assert (work / "app.py").exists()
    assert not (work / "solution").exists()


def test_selection_filters_without_a_runner() -> None:
    tasks = [
        EvalTask(id="a", category="edit", prompt="p", root=Path("."), tags=("fast",)),
        EvalTask(id="b", category="ask", prompt="p", root=Path("."), regression_gate=True),
        EvalTask(id="c", category="edit", prompt="p", root=Path(".")),
    ]
    assert [t.id for t in SuiteSelection(categories=frozenset({"edit"})).apply(tasks)] == ["a", "c"]
    assert [t.id for t in SuiteSelection(tags=frozenset({"fast"})).apply(tasks)] == ["a"]
    assert [t.id for t in SuiteSelection(regression_gate_only=True).apply(tasks)] == ["b"]
    assert [t.id for t in SuiteSelection(ids=frozenset({"c"})).apply(tasks)] == ["c"]
    assert [t.id for t in SuiteSelection(limit=2).apply(tasks)] == ["a", "b"]
    assert [t.id for t in SuiteSelection().apply(tasks)] == ["a", "b", "c"]


def test_a_parallel_of_zero_is_rejected_at_construction() -> None:
    with pytest.raises(ValueError, match="parallel"):
        RunnerConfig(parallel=0)


async def test_a_task_with_no_verify_script_is_reported_unverifiable(tmp_path: Path) -> None:
    root = tmp_path / "suite"
    write_task(root, "no-gate", fixture={"app.py": BROKEN_APP})
    write_manifest(root, ["no-gate"])
    tasks = list(load_suite(root))
    adapter = v2_adapter(scripted_factory([Step(tool="read", path="app.py")]))
    report = await run_suite(tasks, adapter)
    assert report.rows[0].status == "unverifiable"
    assert report.overall.unverifiable == 1
    assert "no verify.sh" in report.rows[0].detail


async def test_asking_for_network_turns_the_skip_into_a_loud_error(tmp_path: Path) -> None:
    """An operator who passed --allow-network is told cloning is unimplemented."""
    root = tmp_path / "suite"
    write_task(
        root,
        "swe-1",
        category="repo",
        extra_toml='git_sha = "deadbeef"\ngit_url = "https://example.invalid/r.git"\n',
    )
    write_manifest(root, ["swe-1"])
    tasks = list(load_suite(root))
    report = await run_suite(tasks, _never_called, config=RunnerConfig(allow_network=True))
    assert report.rows[0].status == "error"
    assert report.overall.skipped == 0
    assert "does not clone" in report.rows[0].detail
