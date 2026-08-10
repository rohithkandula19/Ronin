"""The verify layer's frozen-type invariants.

Each validator below encodes a rule the modules rely on downstream, and none of
them had a test — so deleting any `raise` was a silent no-op. `core/types.py` is
held to 100% on exactly this class of check; these are the same kind of guard
living in a different package.

Two are worth more than their line count. `CommandOutcome` refusing
`failure=... , exit_code=0` is what stops "the command did not run" from being
read as success by every `ok` check in the layer. `DetectedCommand` requiring
`evidence` is what makes `/doctor` able to name where a command came from — the
module's stated position is that a command with no evidence is a guess.
"""

from __future__ import annotations

import pytest

from ronin.verify.runner import (
    CommandFailure,
    CommandOutcome,
    PlanStep,
    Scope,
)
from ronin.verify.spec import CommandKind, DetectedCommand, VerifySpec

# --------------------------------------------------------------------------- #
# CommandOutcome
# --------------------------------------------------------------------------- #


def test_a_command_outcome_needs_an_argv() -> None:
    with pytest.raises(ValueError, match="argv must be non-empty"):
        CommandOutcome(argv=(), exit_code=0)


def test_a_command_that_did_not_run_cannot_report_success() -> None:
    """The invariant that keeps a missing binary from reading as a pass.

    `ok` is `exit_code == 0 and failure is NONE`; without this check a
    `NOT_FOUND` with exit 0 would satisfy it, and a verify step that never ran
    would be reported green.
    """
    for failure in (
        CommandFailure.NOT_FOUND,
        CommandFailure.TIMED_OUT,
        CommandFailure.NOT_PERMITTED,
    ):
        with pytest.raises(ValueError, match="did not run did not succeed"):
            CommandOutcome(argv=("pytest",), exit_code=0, failure=failure)


def test_a_command_outcome_rejects_negative_time() -> None:
    with pytest.raises(ValueError, match="duration_s must be >= 0"):
        CommandOutcome(argv=("pytest",), exit_code=0, duration_s=-0.5)


def test_ok_requires_both_a_zero_exit_and_no_failure() -> None:
    assert CommandOutcome(argv=("pytest",), exit_code=0).ok
    assert not CommandOutcome(argv=("pytest",), exit_code=1).ok
    assert not CommandOutcome(argv=("pytest",), exit_code=127, failure=CommandFailure.NOT_FOUND).ok


@pytest.mark.parametrize(
    ("failure", "exit_code", "expected"),
    [
        (CommandFailure.NONE, 3, "exit 3"),
        (CommandFailure.NOT_FOUND, 127, "not found on PATH"),
        (CommandFailure.TIMED_OUT, 124, "timed out"),
        (CommandFailure.NOT_PERMITTED, 126, "refused"),
    ],
)
def test_a_failure_describes_itself_in_terms_a_user_can_act_on(
    failure: CommandFailure, exit_code: int, expected: str
) -> None:
    outcome = CommandOutcome(
        argv=("pytest", "-q"), exit_code=exit_code, output="denied", failure=failure
    )
    assert expected in outcome.describe()
    assert "pytest -q" in outcome.describe()


# --------------------------------------------------------------------------- #
# PlanStep
# --------------------------------------------------------------------------- #


def test_a_plan_step_needs_an_argv() -> None:
    with pytest.raises(ValueError, match="argv must be non-empty"):
        PlanStep(kind=CommandKind.TEST, argv=(), scope=Scope.WHOLE_PROJECT, why="because")


def test_a_plan_step_must_explain_itself() -> None:
    """ "An unexplained step cannot be audited" — the user is asked to approve
    these, and "trust me" is not a reason someone can evaluate."""
    with pytest.raises(ValueError, match="why is required"):
        PlanStep(kind=CommandKind.TEST, argv=("pytest",), scope=Scope.WHOLE_PROJECT, why="")


def test_a_plan_step_renders_its_command_for_display() -> None:
    step = PlanStep(
        kind=CommandKind.TEST,
        argv=("uv", "run", "pytest", "-q"),
        scope=Scope.CHANGED_FILES,
        why="one file changed",
    )
    assert step.display == "uv run pytest -q"


# --------------------------------------------------------------------------- #
# DetectedCommand and VerifySpec
# --------------------------------------------------------------------------- #


def test_a_detected_command_needs_a_non_empty_argv() -> None:
    with pytest.raises(ValueError, match="argv must be non-empty"):
        DetectedCommand(argv=(), source="pyproject.toml", evidence="x")


def test_a_detected_command_rejects_a_blank_argument() -> None:
    """A stray empty string becomes an empty shell word that most tools read as
    "the current directory", quietly widening a scoped command."""
    with pytest.raises(ValueError, match="empty argument"):
        DetectedCommand(argv=("pytest", ""), source="pyproject.toml", evidence="x")


def test_a_detected_command_must_name_its_source_and_evidence() -> None:
    with pytest.raises(ValueError, match="source is required"):
        DetectedCommand(argv=("pytest",), source="", evidence="x")
    with pytest.raises(ValueError, match="evidence is required"):
        DetectedCommand(argv=("pytest",), source="pyproject.toml", evidence="")


def test_spec_notes_must_be_a_tuple_so_the_spec_stays_frozen() -> None:
    """A list here would let a caller mutate a spec other code already read."""
    with pytest.raises(TypeError, match="must be a tuple"):
        VerifySpec(notes=["not a tuple"])  # type: ignore[arg-type]
