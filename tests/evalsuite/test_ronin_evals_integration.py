"""The real objects wired together: loader → runner → taxonomy → report → scoreboard.

Fakes appear at exactly two seams — the model (an ``AgentFactory``) and, where a test
is about argv rather than about a process, the subprocess runner. ``verify.sh`` runs
through real ``bash`` here, because "the pass criterion actually executed" is the one
thing a fake cannot establish.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from evals_harness import (
    CONDITIONAL_VERIFY,
    Step,
    frozen_clock,
    scripted_factory,
    write_manifest,
    write_task,
)

from ronin.core.types import (
    Budget,
    Event,
    ToolEnd,
    ToolResult,
    ToolStart,
    TurnEnd,
    TurnStart,
    TurnState,
)
from ronin.core.types import Error as ErrorEvent
from ronin.evals import (
    EvalRunner,
    RunnerConfig,
    build_report,
    load_suite,
    report_json,
    report_markdown,
    scoreboard,
    scoreboard_markdown,
    v2_adapter,
)
from ronin.evals.adapters import (
    changed_paths,
    ledger_usage,
    outcome_from_events,
    relative_to,
    tree_digest,
    usage_from_budget,
    v1_adapter,
    v1_argv,
)
from ronin.evals.report import ABSENT
from ronin.evals.task import SUITE_RELATIVE_ROOT
from ronin.evals.taxonomy import FailureClass
from ronin.verify.runner import CommandOutcome

BROKEN_APP = "def add(a, b):\n    return a - b\n"
FIXED_APP = "def add(a, b):\n    return a + b\n"

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_SUITE = REPO_ROOT / SUITE_RELATIVE_ROOT


# --------------------------------------------------------------------------- #
# End to end over a synthetic suite, with real bash
# --------------------------------------------------------------------------- #


def build_suite(root: Path) -> None:
    write_task(
        root,
        "fix-add",
        category="edit",
        prompt="make add return the sum",
        fixture={"app.py": BROKEN_APP},
        solution={"app.py": FIXED_APP},
        verify=CONDITIONAL_VERIFY,
        files_expected=["app.py"],
    )
    write_task(
        root,
        "wrong-file",
        category="edit",
        prompt="make add return the sum",
        fixture={"app.py": BROKEN_APP},
        solution={"app.py": FIXED_APP},
        verify=CONDITIONAL_VERIFY,
        files_expected=["app.py"],
    )
    write_task(
        root,
        "needs-clone",
        category="repo",
        prompt="fix the upstream bug",
        extra_toml='git_sha = "deadbeef"\ngit_url = "https://example.invalid/r.git"\n',
    )
    write_manifest(root, ["fix-add", "wrong-file", "needs-clone"])


async def test_the_whole_pipeline_runs_offline_and_reports_honestly(tmp_path: Path) -> None:
    root = tmp_path / "suite"
    build_suite(root)
    tasks = load_suite(root)
    assert [task.id for task in tasks] == ["fix-add", "wrong-file", "needs-clone"]

    # One agent, one behaviour: it always writes the fix to `app.py`. That passes
    # `fix-add`; `wrong-file` is scripted separately below.
    good = v2_adapter(
        scripted_factory(
            [Step(tool="read", path="app.py"), Step(tool="edit", path="app.py", content=FIXED_APP)],
            budget=Budget(spent_tokens=900, spent_usd=0.01),
        )
    )
    runner = EvalRunner(
        good,
        config=RunnerConfig(parallel=2, label="v2-fake", model="scripted", suite_root=str(root)),
        clock=frozen_clock(),
    )
    report = await runner.run(tasks)

    assert report.overall.tasks == 3
    assert report.overall.skipped == 1
    assert report.overall.attempted == 2
    assert report.overall.passed == 2
    assert report.overall.pass_rate == 1.0
    assert report.per_category["repo"].pass_rate is None
    assert report.taxonomy.failures == 0

    payload = json.loads(report_json(report))
    assert payload["label"] == "v2-fake"
    assert [entry["id"] for entry in payload["tasks"]] == ["fix-add", "wrong-file", "needs-clone"]
    assert payload["tasks"][2]["status"] == "skip"
    assert payload["distributions"]["tokens"]["count"] == 2

    markdown = report_markdown(report)
    assert "# eval report — v2-fake" in markdown
    assert "pass rate: 100.0%" in markdown
    # The all-skipped category has no rate, and says so rather than showing 0%.
    assert f"| repo | {ABSENT} |" in markdown


async def test_two_adapters_over_one_suite_produce_a_scoreboard(tmp_path: Path) -> None:
    root = tmp_path / "suite"
    build_suite(root)
    tasks = load_suite(root)

    fixer = v2_adapter(scripted_factory([Step(tool="edit", path="app.py", content=FIXED_APP)]))
    misser = v2_adapter(scripted_factory([Step(tool="edit", path="notes.md", content="hi\n")]))

    base = await EvalRunner(fixer, config=RunnerConfig(label="base"), clock=frozen_clock()).run(
        tasks
    )
    candidate = await EvalRunner(
        misser, config=RunnerConfig(label="candidate"), clock=frozen_clock()
    ).run(tasks)

    board = scoreboard([base, candidate])
    assert board.labels == ("base", "candidate")
    assert set(board.regressions) == {"fix-add", "wrong-file"}
    assert board.fixes == ()
    rendered = scoreboard_markdown(board)
    assert "| base |" in rendered
    assert "`fix-add`" in rendered

    assert FailureClass.WRONG_FILE in candidate.rows[0].classes


# --------------------------------------------------------------------------- #
# The real fixture tree, when agent A's tasks are present
# --------------------------------------------------------------------------- #


def real_suite_tasks() -> list[object]:
    from ronin.evals.task import load_tasks

    return list(load_tasks(REAL_SUITE))


async def test_a_handful_of_real_tasks_run_end_to_end(tmp_path: Path) -> None:
    """Runs the committed fixtures under ``tests/evals/`` with a fake model.

    Skipped — not silently passed — while the fixture tree is empty, so this test
    starts exercising the real suite the moment the fixtures land.
    """
    if not REAL_SUITE.is_dir():
        pytest.skip(f"{REAL_SUITE} does not exist yet")
    tasks = real_suite_tasks()
    if not tasks:
        pytest.skip(
            f"{REAL_SUITE} holds no task.toml yet "
            f"(only {sorted(p.name for p in REAL_SUITE.iterdir())})"
        )

    chosen = tasks[:5]
    adapter = v2_adapter(scripted_factory([Step(tool="read", path=".")]))
    runner = EvalRunner(
        adapter,
        config=RunnerConfig(parallel=2, label="smoke", suite_root=str(REAL_SUITE)),
        clock=frozen_clock(),
    )
    report = await runner.run(chosen)  # type: ignore[arg-type]

    assert report.overall.tasks == len(chosen)
    # Every attempted task lands in exactly one bucket, and every failure carries at
    # least one class — including UNCLASSIFIED, which is the honest one.
    for result in report.results:
        assert result.skipped or result.passed or result.findings
    assert report_json(report)
    assert report_markdown(report)


def test_the_real_manifest_agrees_with_the_real_tree() -> None:
    """The manifest/tree check, run against the committed suite.

    Skipped while a ``_staging`` directory exists: that is the fixture author's own
    marker for "these tasks are written but not placed yet", and a check that fails
    during a migration it can see in progress is a check people learn to ignore. It
    becomes a hard gate the moment staging is gone.
    """
    if not (REAL_SUITE / "manifest.toml").is_file():
        pytest.skip(f"{REAL_SUITE / 'manifest.toml'} does not exist yet")
    staging = REAL_SUITE / "_staging"
    if staging.is_dir():
        pytest.skip(f"{staging} still holds unplaced tasks; the suite is mid-migration")
    assert load_suite(REAL_SUITE)


# --------------------------------------------------------------------------- #
# The v2 adapter's folding, from events
# --------------------------------------------------------------------------- #


def test_events_fold_into_the_evidence_the_taxonomy_needs(tmp_path: Path) -> None:
    workspace = tmp_path / "work"
    workspace.mkdir()
    events: list[Event] = [
        TurnStart(turn_index=0),
        ToolStart(tool_use_id="1", name="read", arguments={"path": "app.py"}),
        ToolEnd(tool_use_id="1", name="read", result=ToolResult(ok=True, content="src")),
        ToolStart(tool_use_id="2", name="edit", arguments={"path": "app.py", "old_string": "x"}),
        ToolEnd(
            tool_use_id="2",
            name="edit",
            result=ToolResult(ok=False, error="old_string appears 3 times in app.py"),
        ),
        TurnStart(turn_index=1),
        ToolStart(tool_use_id="3", name="edit", arguments={"path": "app.py"}),
        ToolEnd(
            tool_use_id="3",
            name="edit",
            result=ToolResult(ok=True, content="edited", artifacts=(str(workspace / "app.py"),)),
        ),
        TurnEnd(turn_index=1, state=TurnState.DONE, stop_reason="no_tool_calls"),
    ]
    outcome = outcome_from_events(
        events, workspace=workspace, final_text="fixed it", registered_tools=("read", "edit")
    )
    assert outcome.turns_used == 2
    assert [record.name for record in outcome.tool_calls] == ["read", "edit", "edit"]
    assert outcome.edited_paths == ("app.py",)
    assert outcome.tool_calls[1].ok is False
    assert outcome.stop_reason == "no_tool_calls"
    assert outcome.stalled is False
    # The fingerprint is core.loop's, so the taxonomy and the loop agree on sameness.
    assert outcome.tool_calls[0].fingerprint == 'read:{"path": "app.py"}'


def test_a_stall_reaches_the_outcome_from_either_signal(tmp_path: Path) -> None:
    by_turn_end = outcome_from_events(
        [TurnEnd(turn_index=0, state=TurnState.ERROR, stop_reason="stalled")],
        workspace=tmp_path,
    )
    by_error = outcome_from_events(
        [ErrorEvent(message="stalled: repeated 3 times", kind="stalled")], workspace=tmp_path
    )
    assert by_turn_end.stalled is True
    assert by_error.stalled is True


def test_an_edit_outside_the_workspace_is_kept_verbatim_as_evidence(tmp_path: Path) -> None:
    """Dropping it would turn the most interesting failure into 'edited nothing'."""
    workspace = tmp_path / "work"
    workspace.mkdir()
    outside = tmp_path / "elsewhere.py"
    outside.write_text("x\n", encoding="utf-8")
    outcome = outcome_from_events(
        [
            ToolStart(tool_use_id="1", name="write", arguments={"path": str(outside)}),
            ToolEnd(
                tool_use_id="1",
                name="write",
                result=ToolResult(ok=True, content="wrote", artifacts=(str(outside),)),
            ),
        ],
        workspace=workspace,
    )
    assert outcome.edited_paths == (outside.as_posix(),)
    assert relative_to(str(workspace / "a.py"), workspace) == "a.py"


def test_a_budget_with_no_tokens_is_unreported_not_free() -> None:
    silent = usage_from_budget(Budget(), requests=3)
    assert silent.reported is False
    assert silent.unreported_requests == 3
    assert silent.render_tokens() == "unreported"

    spoke = usage_from_budget(Budget(spent_tokens=120, spent_usd=0.5), requests=3)
    assert spoke.reported is True
    assert spoke.priced is True
    assert spoke.render_tokens() == "120"


def test_usage_can_be_read_out_of_a_real_ledger(tmp_path: Path) -> None:
    """sqlite is local, so the ledger seam is exercised for real rather than faked."""
    from ronin.providers.accounting import Ledger
    from ronin.providers.router import ModelSpec, Role
    from ronin.providers.types import Usage

    ledger = Ledger(tmp_path / "ledger.db", clock=lambda: 0.0)
    priced = ModelSpec(name="m", provider="p", model="m-1", price_in=1.0, price_out=2.0)
    ledger.record(
        session_id="task-1",
        request_id="r1",
        role=Role.MAIN,
        spec=priced,
        usage=Usage(input_tokens=1_000, output_tokens=500),
    )
    ledger.record(
        session_id="task-1",
        request_id="r2",
        role=Role.MAIN,
        spec=priced,
        usage=Usage(),
        reported=False,
    )
    usage = ledger_usage(ledger, "task-1")
    assert usage.requests == 2
    assert usage.unreported_requests == 1
    assert usage.reported is True  # one request did report
    assert usage.total_tokens == 1_500
    assert usage.cost_usd == pytest.approx(0.002)


def test_a_missing_ledger_yields_unreported_rather_than_raising() -> None:
    assert ledger_usage(object(), "task-1").reported is False


# --------------------------------------------------------------------------- #
# The v1 adapter — best effort, argv and diff only
# --------------------------------------------------------------------------- #


def test_the_v1_adapter_builds_the_legacy_cli_invocation(tmp_path: Path) -> None:
    from evals_harness import task as make_task

    argv = v1_argv(make_task("t", prompt="fix the bug", max_turns=9), tmp_path)
    assert argv[:2] == ("ronin", "code")
    assert "fix the bug" in argv
    assert "--yolo" in argv
    assert argv[argv.index("--max-steps") + 1] == "9"
    assert argv[argv.index("--root") + 1] == str(tmp_path)


async def test_the_v1_adapter_finds_edits_by_hashing_the_tree(tmp_path: Path) -> None:
    from evals_harness import task as make_task

    workspace = tmp_path / "work"
    workspace.mkdir()
    (workspace / "app.py").write_text(BROKEN_APP, encoding="utf-8")
    (workspace / "untouched.py").write_text("x = 1\n", encoding="utf-8")

    async def fake_cli(argv: list[str], *, cwd: Path, **_: object) -> CommandOutcome:
        (Path(cwd) / "app.py").write_text(FIXED_APP, encoding="utf-8")
        return CommandOutcome(argv=tuple(argv), exit_code=0, output="done")

    outcome = await v1_adapter(command_runner=fake_cli)(make_task("t"), workspace)
    assert outcome.edited_paths == ("app.py",)
    assert outcome.error == ""
    assert any("best-effort" in note for note in outcome.notes)


async def test_a_failing_v1_cli_becomes_a_recorded_error(tmp_path: Path) -> None:
    from evals_harness import task as make_task

    async def missing(argv: list[str], *, cwd: Path, **_: object) -> CommandOutcome:
        from ronin.verify.runner import CommandFailure

        return CommandOutcome(
            argv=tuple(argv),
            exit_code=127,
            output="ronin: command not found",
            failure=CommandFailure.NOT_FOUND,
        )

    outcome = await v1_adapter(command_runner=missing)(make_task("t"), tmp_path)
    assert "not found on PATH" in outcome.error


def test_identical_bytes_are_not_an_edit(tmp_path: Path) -> None:
    """A rewrite with the same contents did not change anything; mtime would disagree."""
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    before = tree_digest(tmp_path)
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    assert changed_paths(before, tree_digest(tmp_path)) == ()
    (tmp_path / "a.py").write_text("x = 2\n", encoding="utf-8")
    assert changed_paths(before, tree_digest(tmp_path)) == ("a.py",)


# --------------------------------------------------------------------------- #
# The taxonomy, end to end through the runner
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("steps", "expected"),
    [
        (
            [Step(tool="edit", path="notes.md", content="hi\n")],
            FailureClass.WRONG_FILE,
        ),
        (
            [Step(tool="edit", ok=False, error="old_string appears 4 times in app.py")],
            FailureClass.EDIT_AMBIGUITY,
        ),
        ([], FailureClass.GAVE_UP_EARLY),
        (
            [Step(tool="str_replace_editor", ok=False, error="tool is not registered")],
            FailureClass.HALLUCINATED_API,
        ),
    ],
)
async def test_each_class_is_reachable_through_the_real_runner(
    tmp_path: Path, steps: list[Step], expected: FailureClass
) -> None:
    root = tmp_path / "suite"
    write_task(
        root,
        "fix-add",
        fixture={"app.py": BROKEN_APP},
        verify=CONDITIONAL_VERIFY,
        files_expected=["app.py"],
    )
    write_manifest(root, ["fix-add"])
    report = await EvalRunner(
        v2_adapter(scripted_factory(steps)), clock=frozen_clock()
    ).run(load_suite(root))
    assert expected in report.rows[0].classes


async def test_a_looping_agent_is_classified_looped_through_the_runner(tmp_path: Path) -> None:
    root = tmp_path / "suite"
    write_task(
        root,
        "loop",
        fixture={"app.py": BROKEN_APP},
        verify=CONDITIONAL_VERIFY,
        files_expected=["app.py"],
    )
    write_manifest(root, ["loop"])
    same = [Step(tool="grep", arguments={"pattern": "add"}) for _ in range(4)]
    report = await EvalRunner(
        v2_adapter(scripted_factory(same)), clock=frozen_clock()
    ).run(load_suite(root))
    assert FailureClass.LOOPED in report.rows[0].classes


def test_a_report_over_no_results_renders_without_inventing_anything() -> None:
    report = build_report([], label="empty")
    markdown = report_markdown(report)
    assert "0.0%" not in markdown
    assert ABSENT in markdown
    assert json.loads(report_json(report))["tasks"] == []
