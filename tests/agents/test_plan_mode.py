"""plan mode: the registry guarantee, and the plan → approval → objective path."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from agents_harness import MislabelledWrite, context, registry, use

from ronin.agents.plan_mode import (
    FORBIDDEN_IN_PLAN_MODE,
    MAX_PLAN_STEPS,
    PLAN_MODE_TOOLS,
    PLAN_MODEL_ROLE,
    Plan,
    PlanApproval,
    PlanModeViolation,
    PlanParseError,
    PlanStep,
    approve,
    assert_read_only,
    enter_plan_mode,
    objective_message,
    parse_plan,
    pinned_objective,
    plan_registry,
    reject,
)
from ronin.core.types import DangerLevel, Mode, Role
from ronin.tools.files import WriteTool
from ronin.tools.registry import ToolRegistry

# --------------------------------------------------------------------------- #
# The registry guarantee
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("forbidden", sorted(FORBIDDEN_IN_PLAN_MODE))
def test_a_plan_registry_does_not_contain_any_mutating_tool(
    tmp_path: Path, forbidden: str
) -> None:
    plan = plan_registry(registry(tmp_path))
    assert plan.get(forbidden) is None


def test_plan_mode_offers_exactly_the_read_only_four(tmp_path: Path) -> None:
    plan = plan_registry(registry(tmp_path))
    assert [spec.name for spec in plan.specs()] == list(PLAN_MODE_TOOLS)
    assert all(spec.danger_level is DangerLevel.READ_ONLY for spec in plan.specs())


async def test_calling_write_in_plan_mode_is_an_unknown_tool_not_a_refusal(
    tmp_path: Path,
) -> None:
    """The tool is absent, so the failure is 'no such tool' — nothing to bypass."""
    plan = plan_registry(registry(tmp_path))
    result = await plan.execute(use("write", path="src/app.py", content="x"))
    assert not result.ok
    assert "no tool called 'write'" in result.error


async def test_a_read_tool_still_works_in_plan_mode(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("hello\n")
    plan = plan_registry(registry(tmp_path))
    result = await plan.execute(use("read", path="note.txt"))
    assert result.ok
    assert "hello" in result.content


def test_adding_a_mutating_tool_to_a_plan_registry_fails_loudly(tmp_path: Path) -> None:
    base = registry(tmp_path)
    widened = ToolRegistry([*plan_registry(base).tools(), WriteTool()], context(tmp_path))
    with pytest.raises(PlanModeViolation) as caught:
        assert_read_only(widened)
    assert "write" in str(caught.value)
    assert "MUTATING" in str(caught.value)


def test_a_tool_that_lies_about_its_danger_level_is_caught_by_name(
    tmp_path: Path,
) -> None:
    sneaky = ToolRegistry([MislabelledWrite()], context(tmp_path))
    with pytest.raises(PlanModeViolation, match="forbidden by name"):
        assert_read_only(sneaky)


def test_a_tool_requiring_approval_cannot_be_in_plan_mode(tmp_path: Path) -> None:
    """Nothing in plan mode changes anything, so nothing should need a yes."""

    class Gated(WriteTool):
        name = "confirm_thing"
        danger_level = DangerLevel.READ_ONLY
        requires_approval = True

    with pytest.raises(PlanModeViolation, match="require approval"):
        assert_read_only(ToolRegistry([Gated()], context(tmp_path)))


def test_plan_mode_refuses_to_build_from_a_session_missing_a_tool(tmp_path: Path) -> None:
    """A session with no `task` must pass its own names, not get a smaller set."""
    with pytest.raises(ValueError, match="unknown tools"):
        plan_registry(registry(tmp_path, with_task=False))


def test_planning_gets_its_own_read_set(tmp_path: Path) -> None:
    base = registry(tmp_path)
    own = context(tmp_path)
    plan = plan_registry(base, ctx=own)
    assert plan.ctx is own
    assert plan.ctx is not base.ctx


def test_entering_plan_mode_names_the_plan_role_without_importing_the_router(
    tmp_path: Path,
) -> None:
    setup = enter_plan_mode(registry(tmp_path))
    assert setup.model_role == PLAN_MODEL_ROLE == "plan"
    assert setup.mode is Mode.PLAN
    assert "PLAN MODE" in setup.system_prompt


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

NUMBERED = """\
Here is what I found.

1. Read the failing test
2. Fix the off-by-one in parse()
3. Run the suite
"""

BULLETS = """\
- Read the failing test
* Fix the off-by-one in parse()
• Run the suite
"""

HEADED_STEPS = """\
## Plan
### Step 1: Read the failing test
### Step 2: Fix the off-by-one in parse()
### Step 3: Run the suite
"""

PARENS = """\
1) Read the failing test
2) Fix the off-by-one in parse()
3) Run the suite
"""


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (NUMBERED, ["Read the failing test", "Fix the off-by-one in parse()", "Run the suite"]),
        (BULLETS, ["Read the failing test", "Fix the off-by-one in parse()", "Run the suite"]),
        (
            HEADED_STEPS,
            ["Read the failing test", "Fix the off-by-one in parse()", "Run the suite"],
        ),
        (PARENS, ["Read the failing test", "Fix the off-by-one in parse()", "Run the suite"]),
    ],
)
def test_the_parser_accepts_every_shape_a_model_writes_a_list_in(
    raw: str, expected: list[str]
) -> None:
    plan = parse_plan(raw)
    assert [step.text for step in plan.steps] == expected
    assert [step.index for step in plan.steps] == [1, 2, 3]


def test_prose_before_the_list_becomes_the_rationale() -> None:
    plan = parse_plan(NUMBERED)
    assert plan.rationale == "Here is what I found."
    assert plan.raw == NUMBERED


def test_a_heading_is_structure_and_never_lands_in_the_rationale() -> None:
    plan = parse_plan(HEADED_STEPS)
    assert plan.rationale == ""


def test_bold_and_backticks_are_stripped_from_a_step() -> None:
    plan = parse_plan("1. Rename **handler** to `handle_request`\n2. Update the caller\n")
    assert plan.steps[0].text == "Rename handler to handle_request"


def test_an_indented_continuation_line_extends_the_step_above_it() -> None:
    plan = parse_plan(
        "1. Raise HandlerError from handler()\n"
        "   (the caller already has a try block)\n"
        "2. Add the test\n"
    )
    assert len(plan.steps) == 2
    assert plan.steps[0].text == (
        "Raise HandlerError from handler() (the caller already has a try block)"
    )


def test_a_diff_in_a_fence_does_not_explode_into_one_step_per_line() -> None:
    """The real failure: `-` lines in a fenced diff read as dozens of bullets."""
    plan = parse_plan(
        "1. Apply this patch to src/app.py:\n"
        "```diff\n"
        "- return None\n"
        "- # unreachable\n"
        "+ raise HandlerError(path)\n"
        "```\n"
        "2. Run the suite\n"
    )
    assert len(plan.steps) == 2
    assert plan.steps[1].text == "Run the suite"


def test_a_decimal_in_the_rationale_is_not_read_as_a_step() -> None:
    plan = parse_plan("The retry is 3.5x slower than it needs to be.\n1. Cache the token\n")
    assert [step.text for step in plan.steps] == ["Cache the token"]
    assert "3.5x" in plan.rationale


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   \n\n",
        "I had a look and honestly it seems fine to me.",
    ],
)
def test_an_unparseable_plan_is_an_error_that_keeps_the_raw_text(raw: str) -> None:
    with pytest.raises(PlanParseError) as caught:
        parse_plan(raw)
    assert caught.value.raw == raw
    assert caught.value.reason


def test_a_wall_of_bullets_is_refused_rather_than_approved_as_a_plan() -> None:
    raw = "\n".join(f"- step {n}" for n in range(MAX_PLAN_STEPS + 5))
    with pytest.raises(PlanParseError, match="more than a reviewable plan"):
        parse_plan(raw)


# --------------------------------------------------------------------------- #
# The plan value, approval, and pinning
# --------------------------------------------------------------------------- #


def test_a_plan_cannot_be_constructed_empty() -> None:
    with pytest.raises(ValueError, match="cannot be approved"):
        Plan(steps=())


def test_plan_steps_must_be_numbered_in_order() -> None:
    with pytest.raises(ValueError, match=re.escape("numbered 1..n")):
        Plan(steps=(PlanStep(index=2, text="second"),))


def test_an_empty_step_is_rejected() -> None:
    with pytest.raises(ValueError, match="approves nothing"):
        PlanStep(index=1, text="  ")


def test_the_pinned_objective_carries_every_step_and_forbids_substitution() -> None:
    plan = parse_plan(NUMBERED)
    text = pinned_objective(plan)
    for step in plan.steps:
        assert step.text in text
    assert "STANDING OBJECTIVE" in text
    assert "do not substitute a different plan" in text
    assert plan.rationale in text


def test_the_objective_becomes_a_user_message_so_it_survives_the_prefix() -> None:
    plan = parse_plan(NUMBERED)
    message = objective_message(plan)
    assert message.role is Role.USER
    assert message.text == pinned_objective(plan)


def test_approval_flips_the_mode_and_pins_the_plan() -> None:
    decision = approve(parse_plan(NUMBERED), note="go")
    assert decision.approved
    assert decision.mode is Mode.AUTO_EDIT
    assert "STANDING OBJECTIVE" in decision.objective
    assert decision.note == "go"


def test_rejection_leaves_the_session_in_plan_mode_with_nothing_pinned() -> None:
    decision = reject(parse_plan(NUMBERED), note="wrong file")
    assert decision.mode is Mode.PLAN
    assert decision.objective == ""


def test_approving_into_plan_mode_is_a_contradiction_and_raises() -> None:
    with pytest.raises(ValueError, match="leaving plan mode"):
        PlanApproval(plan=parse_plan(NUMBERED), approved=True, execute_mode=Mode.PLAN)


def test_an_approval_carries_the_plan_it_approved() -> None:
    """After a compaction the transcript may not hold the plan the user read."""
    plan = parse_plan(NUMBERED)
    decision = approve(plan)
    assert decision.plan is plan
    assert decision.plan.raw == NUMBERED
