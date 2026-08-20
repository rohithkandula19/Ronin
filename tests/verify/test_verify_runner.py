"""The runner: real argv plumbing, computed relevance, failures as observations."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from verify_harness import PYTEST_ONE_FAILURE, RUFF_ONE_ERROR, ScriptedRunner, make_python_project

from ronin.core.types import Mode
from ronin.verify.runner import (
    MAX_OUTPUT_CHARS,
    PYTHON_RULES,
    TYPESCRIPT_RULES,
    CommandFailure,
    CommandOutcome,
    Scope,
    as_tool_result,
    candidate_test_paths,
    clamp_output,
    owning_rules,
    plan_for_changes,
    resolve_test_targets,
    root_relative,
    rules_for_path,
    run_command,
    run_plan,
    should_verify,
)
from ronin.verify.spec import CommandKind, DetectedCommand, VerifySpec, detect_verify_spec

# --------------------------------------------------------------------------- #
# Clamping and outcomes
# --------------------------------------------------------------------------- #


def test_clamping_marks_exactly_what_it_cut_and_keeps_both_ends() -> None:
    text = "HEAD" + ("x" * 200) + "TAIL"
    clamped = clamp_output(text, limit=100)
    assert clamped.startswith("HEAD")
    assert clamped.endswith("TAIL")
    assert "characters cut from the middle" in clamped
    assert f"{len(text) - 100:,}" in clamped


def test_clamping_is_a_no_op_under_the_limit() -> None:
    assert clamp_output("short", limit=MAX_OUTPUT_CHARS) == "short"


def test_an_outcome_cannot_claim_success_while_reporting_a_failure() -> None:
    with pytest.raises(ValueError, match="did not succeed"):
        CommandOutcome(argv=("x",), exit_code=0, failure=CommandFailure.TIMED_OUT)


@pytest.mark.parametrize(
    ("failure", "fragment"),
    [
        (CommandFailure.NOT_FOUND, "not found on PATH"),
        (CommandFailure.TIMED_OUT, "timed out"),
    ],
)
def test_describe_names_the_failure_mode(failure: CommandFailure, fragment: str) -> None:
    outcome = CommandOutcome(argv=("x",), exit_code=127, failure=failure, duration_s=1.0)
    assert fragment in outcome.describe()


# --------------------------------------------------------------------------- #
# The one real subprocess
# --------------------------------------------------------------------------- #


async def test_run_command_passes_argv_verbatim_and_returns_the_exit_code(
    tmp_path: Path,
) -> None:
    """The real-subprocess proof: argv is not shell-split, and the code comes back.

    ``"a b"`` arriving as one argument is the assertion that matters — a shelled-out
    implementation would split it and nothing else in this file would notice.
    """
    outcome = await run_command(
        [sys.executable, "-c", "import sys; print(sys.argv[1:]); sys.exit(3)", "a b", "c"],
        cwd=tmp_path,
    )
    assert outcome.exit_code == 3
    assert outcome.ok is False
    assert outcome.output.strip() == "['a b', 'c']"
    assert outcome.failure is CommandFailure.NONE


async def test_run_command_merges_stderr_into_the_captured_output(tmp_path: Path) -> None:
    outcome = await run_command(
        [sys.executable, "-c", "import sys; sys.stderr.write('boom\\n')"], cwd=tmp_path
    )
    assert outcome.ok
    assert "boom" in outcome.output


async def test_a_missing_binary_is_an_outcome_not_an_exception(tmp_path: Path) -> None:
    outcome = await run_command(["ronin-does-not-exist"], cwd=tmp_path)
    assert outcome.failure is CommandFailure.NOT_FOUND
    assert outcome.exit_code == 127
    assert "command not found" in outcome.output


async def test_a_hanging_command_is_killed_and_reported_as_a_timeout(tmp_path: Path) -> None:
    outcome = await run_command(
        [sys.executable, "-c", "import time; time.sleep(30)"], cwd=tmp_path, timeout=0.5
    )
    assert outcome.failure is CommandFailure.TIMED_OUT
    assert outcome.ok is False


# --------------------------------------------------------------------------- #
# Source path → test path
# --------------------------------------------------------------------------- #


def test_the_three_python_test_conventions_are_all_candidates() -> None:
    candidates = candidate_test_paths("src/pkg/mod.py", PYTHON_RULES)
    assert "src/pkg/test_mod.py" in candidates
    assert "tests/pkg/test_mod.py" in candidates
    assert "tests/test_mod.py" in candidates
    # Same-directory first: it is the least ambiguous match.
    assert candidates[0] == "src/pkg/test_mod.py"


def test_only_test_files_that_exist_are_selected(tmp_path: Path) -> None:
    make_python_project(tmp_path)
    targets = resolve_test_targets("src/pkg/thing.py", root=tmp_path, rules=PYTHON_RULES)
    assert targets.matched == ("tests/pkg/test_thing.py",)
    assert targets.found


def test_a_source_with_no_test_reports_what_it_looked_for(tmp_path: Path) -> None:
    make_python_project(tmp_path)
    targets = resolve_test_targets("src/pkg/orphan.py", root=tmp_path, rules=PYTHON_RULES)
    assert targets.matched == ()
    assert not targets.found
    assert "tests/pkg/test_orphan.py" in targets.considered


def test_a_changed_test_file_is_its_own_target(tmp_path: Path) -> None:
    make_python_project(tmp_path)
    targets = resolve_test_targets("tests/pkg/test_thing.py", root=tmp_path, rules=PYTHON_RULES)
    assert targets.matched == ("tests/pkg/test_thing.py",)


@pytest.mark.parametrize(
    ("path", "name"),
    [
        ("a/b.py", "python"),
        ("a/b.pyi", "python"),
        ("a/b.tsx", "typescript"),
        ("a/b.mjs", "javascript"),
    ],
)
def test_rules_are_selected_by_extension(path: str, name: str) -> None:
    rules = rules_for_path(path)
    assert rules is not None and rules.name == name


def test_an_unknown_extension_has_no_rules() -> None:
    assert rules_for_path("README.md") is None


@pytest.mark.parametrize(
    ("argv", "name"),
    [
        (("mypy",), "python"),
        (("uv", "run", "ruff", "check", "."), "python"),
        (("python", "-m", "pytest"), "python"),
        (("tsc", "--noEmit"), "typescript"),
    ],
)
def test_owning_rules_looks_past_the_environment_prefix(argv: tuple[str, ...], name: str) -> None:
    owner = owning_rules(DetectedCommand(argv=argv, source="s", evidence="e"))
    assert owner is not None and owner.name == name


def test_an_indirect_command_has_no_owning_language() -> None:
    assert owning_rules(DetectedCommand(argv=("make", "test"), source="s", evidence="e")) is None


def test_root_relative_rejects_paths_outside_the_tree(tmp_path: Path) -> None:
    inside = tmp_path / "src" / "a.py"
    inside.parent.mkdir(parents=True)
    inside.write_text("", encoding="utf-8")
    assert root_relative(inside, tmp_path) == "src/a.py"
    assert root_relative("/etc/passwd", tmp_path) is None
    assert root_relative("src/a.py", tmp_path) == "src/a.py"


# --------------------------------------------------------------------------- #
# Planning
# --------------------------------------------------------------------------- #


def test_a_changed_python_file_gets_lint_typecheck_and_only_its_own_tests(
    tmp_path: Path,
) -> None:
    make_python_project(tmp_path)
    spec = detect_verify_spec(tmp_path)

    plan = plan_for_changes(spec, ["src/pkg/thing.py"], root=tmp_path)

    assert [step.kind for step in plan.steps] == [
        CommandKind.LINT,
        CommandKind.TYPECHECK,
        CommandKind.TEST,
    ]
    assert plan.steps[0].argv == ("uv", "run", "ruff", "check", "src/pkg/thing.py")
    assert plan.steps[1].argv == ("uv", "run", "mypy", "src/pkg/thing.py")
    assert plan.steps[2].argv == ("uv", "run", "pytest", "tests/pkg/test_thing.py")
    assert plan.steps[2].scope is Scope.MATCHED_TESTS


def test_a_source_without_tests_is_reported_and_does_not_widen_to_the_full_suite(
    tmp_path: Path,
) -> None:
    make_python_project(tmp_path)
    spec = detect_verify_spec(tmp_path)

    plan = plan_for_changes(spec, ["src/pkg/orphan.py"], root=tmp_path)

    assert not any(step.kind is CommandKind.TEST for step in plan.steps)
    assert any("no test file found for src/pkg/orphan.py" in note for note in plan.notes)
    assert any("full suite was not run in its place" in note for note in plan.notes)


def test_the_full_suite_and_build_only_appear_in_the_end_of_task_pass(tmp_path: Path) -> None:
    make_python_project(tmp_path)
    spec = detect_verify_spec(tmp_path).with_command(
        CommandKind.BUILD, DetectedCommand(argv=("python", "-m", "build"), source="s", evidence="e")
    )

    plan = plan_for_changes(spec, ["src/pkg/thing.py"], root=tmp_path, include_full_suite=True)

    scopes = [(step.kind, step.scope) for step in plan.steps]
    assert scopes[-2:] == [
        (CommandKind.TEST, Scope.FULL_SUITE),
        (CommandKind.BUILD, Scope.FULL_SUITE),
    ]
    assert plan.steps[-2].argv == ("uv", "run", "pytest")


def test_a_missing_test_command_is_stated_rather_than_invented(tmp_path: Path) -> None:
    make_python_project(tmp_path)
    spec = VerifySpec(lint=DetectedCommand(argv=("ruff", "check"), source="s", evidence="e"))

    plan = plan_for_changes(spec, ["src/pkg/thing.py"], root=tmp_path)

    assert not any(step.kind is CommandKind.TEST for step in plan.steps)
    assert any("no test command was detected" in note for note in plan.notes)


def test_an_indirect_test_command_runs_unnarrowed_and_says_why(tmp_path: Path) -> None:
    make_python_project(tmp_path)
    spec = VerifySpec(test=DetectedCommand(argv=("make", "test"), source="Makefile", evidence="e"))

    plan = plan_for_changes(spec, ["src/pkg/thing.py"], root=tmp_path)

    step = next(step for step in plan.steps if step.kind is CommandKind.TEST)
    assert step.argv == ("make", "test")
    assert step.scope is Scope.WHOLE_PROJECT
    assert "cannot be narrowed" in step.why


def test_a_python_linter_is_not_handed_typescript_files(tmp_path: Path) -> None:
    """The polyglot trap: ``ruff check app.ts`` fails for no useful reason."""
    make_python_project(tmp_path)
    (tmp_path / "app.ts").write_text("export const x = 1;\n", encoding="utf-8")
    spec = detect_verify_spec(tmp_path)

    plan = plan_for_changes(spec, ["app.ts"], root=tmp_path)

    assert plan.steps == ()
    assert any("no python file changed" in note for note in plan.notes)


def test_a_whole_project_typechecker_is_never_narrowed_to_one_file(tmp_path: Path) -> None:
    (tmp_path / "app.ts").write_text("export const x = 1;\n", encoding="utf-8")
    spec = VerifySpec(typecheck=DetectedCommand(argv=("tsc", "--noEmit"), source="s", evidence="e"))

    plan = plan_for_changes(spec, ["app.ts"], root=tmp_path, rules=[TYPESCRIPT_RULES])

    step = plan.steps[0]
    assert step.argv == ("tsc", "--noEmit")
    assert step.scope is Scope.WHOLE_PROJECT
    assert "reads project config" in step.why


def test_a_file_with_no_language_rules_is_named_in_the_notes(tmp_path: Path) -> None:
    make_python_project(tmp_path)
    plan = plan_for_changes(detect_verify_spec(tmp_path), ["README.md"], root=tmp_path)
    assert any("no language rules for README.md" in note for note in plan.notes)


def test_a_path_outside_the_repo_is_dropped_with_a_note(tmp_path: Path) -> None:
    make_python_project(tmp_path)
    plan = plan_for_changes(detect_verify_spec(tmp_path), ["/etc/passwd"], root=tmp_path)
    assert any("is outside" in note for note in plan.notes)


# --------------------------------------------------------------------------- #
# Running
# --------------------------------------------------------------------------- #


async def test_every_step_runs_by_default_so_one_round_trip_shows_everything(
    tmp_path: Path,
) -> None:
    make_python_project(tmp_path)
    plan = plan_for_changes(detect_verify_spec(tmp_path), ["src/pkg/thing.py"], root=tmp_path)
    runner = ScriptedRunner(rules=(("ruff", 1, RUFF_ONE_ERROR), ("pytest", 1, PYTEST_ONE_FAILURE)))

    outcome = await run_plan(plan, run=runner, cwd=tmp_path)

    assert len(outcome.results) == 3
    assert len(outcome.failures) == 2
    assert outcome.skipped == ()
    assert runner.cwds == [tmp_path, tmp_path, tmp_path]


async def test_fail_fast_records_what_it_skipped(tmp_path: Path) -> None:
    make_python_project(tmp_path)
    plan = plan_for_changes(detect_verify_spec(tmp_path), ["src/pkg/thing.py"], root=tmp_path)
    runner = ScriptedRunner(rules=(("ruff", 1, RUFF_ONE_ERROR),))

    outcome = await run_plan(plan, run=runner, cwd=tmp_path, stop_after_first_failure=True)

    assert len(outcome.results) == 1
    assert len(outcome.skipped) == 2
    assert "skip" in outcome.render()


async def test_an_empty_plan_is_unknown_not_ok(tmp_path: Path) -> None:
    outcome = await run_plan(
        plan_for_changes(VerifySpec(), [], root=tmp_path), run=ScriptedRunner(), cwd=tmp_path
    )
    assert outcome.unknown
    assert outcome.ok is False


# --------------------------------------------------------------------------- #
# Back to the model
# --------------------------------------------------------------------------- #


async def test_a_failure_comes_back_as_a_failed_tool_result_with_the_output(
    tmp_path: Path,
) -> None:
    make_python_project(tmp_path)
    plan = plan_for_changes(detect_verify_spec(tmp_path), ["src/pkg/thing.py"], root=tmp_path)
    runner = ScriptedRunner(rules=(("pytest", 1, PYTEST_ONE_FAILURE),))

    result = as_tool_result(await run_plan(plan, run=runner, cwd=tmp_path))

    assert result.ok is False
    assert "verify failed (test)" in result.error
    assert "AssertionError: assert 1.0 == 1.01" in result.error
    assert "Fix the cause" in result.error
    assert result.content == ""


async def test_a_pass_is_an_ok_tool_result(tmp_path: Path) -> None:
    make_python_project(tmp_path)
    plan = plan_for_changes(detect_verify_spec(tmp_path), ["src/pkg/thing.py"], root=tmp_path)

    result = as_tool_result(await run_plan(plan, run=ScriptedRunner(), cwd=tmp_path))

    assert result.ok
    assert "all 3 check(s) passed" in result.content


async def test_nothing_to_run_says_so_instead_of_implying_verification(tmp_path: Path) -> None:
    outcome = await run_plan(
        plan_for_changes(VerifySpec(), [], root=tmp_path), run=ScriptedRunner(), cwd=tmp_path
    )
    result = as_tool_result(outcome)
    assert result.ok
    assert "nothing was proven" in result.content
    assert "Do not report success" in result.content


async def test_the_tool_result_is_clamped_deterministically(tmp_path: Path) -> None:
    make_python_project(tmp_path)
    plan = plan_for_changes(detect_verify_spec(tmp_path), ["src/pkg/thing.py"], root=tmp_path)
    runner = ScriptedRunner(rules=(("pytest", 1, "E   AssertionError\n" + "x" * 50_000),))

    result = as_tool_result(await run_plan(plan, run=runner, cwd=tmp_path), limit=500)

    assert "characters cut from the middle" in result.error
    assert len(result.error) < 2_000


@pytest.mark.parametrize(
    ("mode", "changed", "expected"),
    [
        (Mode.PLAN, ["a.py"], False),
        (Mode.ASK, ["a.py"], False),
        (Mode.AUTO_EDIT, ["a.py"], True),
        (Mode.FULL, ["a.py"], True),
        (Mode.FULL, [], False),
    ],
)
def test_should_verify_follows_the_mode_ladder(
    mode: Mode, changed: list[str], expected: bool
) -> None:
    assert should_verify(mode=mode, changed_paths=changed) is expected
