"""todo_write and task.

The todo tests are mostly about the invariants — exactly one thing in progress, and
never zero while work remains — because a plan that says nothing is in progress while
five items are pending is a plan the user cannot read and the model is not following.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from harness import RecordingRunner, call, context

from ronin.core.types import TodoStatus
from ronin.tools import TaskTool, TodoWriteTool, parse_todos, render_todos
from ronin.tools.base import ToolError
from ronin.tools.task import BUILTIN_SUBAGENTS, MAX_TODOS, SubagentType

TODO = TodoWriteTool()


def plan(*items: tuple[str, str]) -> list[dict[str, str]]:
    return [{"content": content, "status": status} for content, status in items]


# --------------------------------------------------------------------------- #
# todo_write
# --------------------------------------------------------------------------- #


async def test_a_plan_is_stored_on_the_context_and_rendered(tmp_path: Path) -> None:
    ctx = context(tmp_path)
    result = await call(
        TODO,
        ctx,
        todos=plan(
            ("Read the test", "completed"),
            ("Fix parse()", "in_progress"),
            ("Run it", "pending"),
        ),
    )
    assert result.ok
    assert "[x] Read the test" in result.content
    assert "[~] Fix parse()" in result.content
    assert "[ ] Run it" in result.content
    assert "(1/3 done)" in result.content
    assert len(ctx.todos) == 3


async def test_more_than_one_in_progress_is_refused(tmp_path: Path) -> None:
    result = await call(
        TODO, context(tmp_path), todos=plan(("A", "in_progress"), ("B", "in_progress"))
    )
    assert not result.ok
    assert "2 todos are in_progress" in result.error
    assert "Exactly one" in result.error


async def test_nothing_in_progress_while_work_remains_is_refused(tmp_path: Path) -> None:
    result = await call(TODO, context(tmp_path), todos=plan(("A", "pending"), ("B", "pending")))
    assert not result.ok
    assert "no todo is in_progress" in result.error


async def test_an_all_completed_plan_is_accepted(tmp_path: Path) -> None:
    result = await call(TODO, context(tmp_path), todos=plan(("A", "completed"), ("B", "completed")))
    assert result.ok
    assert "(2/2 done)" in result.content


async def test_an_empty_plan_is_refused_with_the_alternative(tmp_path: Path) -> None:
    result = await call(TODO, context(tmp_path), todos=[])
    assert not result.ok
    assert "mark every item completed instead" in result.error


async def test_an_over_long_plan_is_refused(tmp_path: Path) -> None:
    huge = plan(*[(f"step {i}", "pending") for i in range(MAX_TODOS + 1)])
    result = await call(TODO, context(tmp_path), todos=huge)
    assert not result.ok
    assert "too many to be a plan" in result.error


async def test_an_unknown_status_lists_the_valid_ones(tmp_path: Path) -> None:
    result = await call(TODO, context(tmp_path), todos=plan(("A", "almost")))
    assert not result.ok
    assert "must be one of" in result.error
    assert "in_progress" in result.error


async def test_a_non_list_is_refused(tmp_path: Path) -> None:
    result = await call(TODO, context(tmp_path), todos="do the thing")
    assert not result.ok
    assert "must be a list" in result.error


async def test_an_empty_content_string_is_refused(tmp_path: Path) -> None:
    result = await call(TODO, context(tmp_path), todos=plan(("   ", "in_progress")))
    assert not result.ok
    assert "is empty" in result.error


def test_a_missing_status_defaults_to_pending() -> None:
    todos = parse_todos([{"content": "A", "status": "in_progress"}, {"content": "B"}])
    assert todos[1].status is TodoStatus.PENDING


def test_either_content_or_subject_is_accepted() -> None:
    """The contract calls it `subject`; models write `content`. Both work."""
    by_content = parse_todos([{"content": "A", "status": "in_progress"}])
    by_subject = parse_todos([{"subject": "A", "status": "in_progress"}])
    assert by_content[0].subject == by_subject[0].subject == "A"


def test_ids_are_positional_and_not_model_supplied() -> None:
    todos = parse_todos([{"content": "A", "status": "in_progress"}, {"content": "B"}])
    assert [t.id for t in todos] == ["todo-1", "todo-2"]


def test_render_is_stable_for_the_same_input() -> None:
    todos = parse_todos(plan(("A", "in_progress")))
    assert render_todos(todos) == render_todos(todos)


def test_the_description_says_three_or_more_steps() -> None:
    """The threshold has to be in the prompt or the model will not apply it."""
    description = TODO.spec().description
    assert "three or more steps" in description
    assert "WHEN NOT TO USE" in description


# --------------------------------------------------------------------------- #
# task
# --------------------------------------------------------------------------- #


async def test_a_subagent_receives_the_prompt_and_its_type(tmp_path: Path) -> None:
    runner = RecordingRunner("found it at src/x.py:12")
    tool = TaskTool(runner)
    result = await call(
        tool, context(tmp_path), prompt="Find the parser", subagent_type="explore"
    )
    assert result.ok
    assert result.content == "found it at src/x.py:12"
    assert len(runner.calls) == 1
    prompt, subagent = runner.calls[0]
    assert prompt == "Find the parser"
    assert subagent.name == "explore"


async def test_only_the_final_summary_comes_back(tmp_path: Path) -> None:
    """The parent must not see intermediate turns — that is the entire point."""
    runner = RecordingRunner("THE CONCLUSION")
    result = await call(
        TaskTool(runner), context(tmp_path), prompt="Sweep the repo", subagent_type="explore"
    )
    assert result.content == "THE CONCLUSION"


async def test_an_unknown_subagent_type_lists_the_available_ones(tmp_path: Path) -> None:
    result = await call(
        TaskTool(RecordingRunner()), context(tmp_path), prompt="x", subagent_type="wizard"
    )
    assert not result.ok
    assert "unknown subagent_type" in result.error
    assert "explore" in result.error


async def test_an_empty_prompt_is_refused(tmp_path: Path) -> None:
    result = await call(
        TaskTool(RecordingRunner()), context(tmp_path), prompt="   ", subagent_type="explore"
    )
    assert not result.ok
    assert "no other context" in result.error


async def test_a_silent_subagent_is_reported_as_a_failure(tmp_path: Path) -> None:
    result = await call(
        TaskTool(RecordingRunner("")), context(tmp_path), prompt="x", subagent_type="explore"
    )
    assert not result.ok
    assert "returned nothing" in result.error
    assert "no transcript" in result.error


def test_the_description_lists_the_registered_types_dynamically() -> None:
    """A model told about a type that does not exist will call it and fail."""
    custom = {
        "auditor": SubagentType(
            name="auditor",
            description="Check a diff for regressions.",
            tools=("read", "grep"),
            system_prompt="Audit.",
        )
    }
    description = TaskTool(RecordingRunner(), types=custom).spec().description
    catalogue = description.split("AVAILABLE SUBAGENT TYPES")[1]
    assert "auditor" in catalogue
    assert "Check a diff for regressions." in catalogue
    assert "explore" not in catalogue


def test_the_builtin_subagents_are_read_only() -> None:
    """Which is what makes spawning one a cheap decision rather than a risky one."""
    writers = {"write", "edit", "multi_edit", "bash"}
    for subagent in BUILTIN_SUBAGENTS.values():
        assert not writers & set(subagent.tools), subagent.name


def test_a_subagent_type_declares_its_own_iteration_cap() -> None:
    assert all(t.max_iterations > 0 for t in BUILTIN_SUBAGENTS.values())


def test_parse_todos_rejects_a_non_object_entry() -> None:
    with pytest.raises(ToolError, match="must be an object"):
        parse_todos(["just a string"])
