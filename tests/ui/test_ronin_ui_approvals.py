"""Exit code 2 must mean *refused*, not *asked*.

``core.loop`` yields ``ApprovalRequest`` before it calls ``policy.approve`` — a UI has
to render the prompt in order to ask. Counting those requests made exit code 2 mean
"this run touched a gated tool", so **every ``--mode auto_edit`` run that wrote a file
exited 2 on a clean run** and listed the successful write under ``approvals_denied``.
An exit code that means two different things is not a signal a script can act on.

These tests pin the distinction at all three call sites that compute it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from ronin.core.types import (
    ApprovalRequest,
    DangerLevel,
    Error,
    Event,
    ToolEnd,
    ToolResult,
    TurnEnd,
    TurnStart,
    TurnState,
)
from ronin.ui.headless import (
    EXIT_ERROR,
    EXIT_NEEDS_APPROVAL,
    EXIT_OK,
    ApprovalTracker,
    OutputFormat,
    run_headless,
)

GATED = ApprovalRequest(
    tool_use_id="t1",
    name="write",
    danger_level=DangerLevel.MUTATING,
    rendered="write notes.txt",
)


def granted() -> ToolEnd:
    return ToolEnd(tool_use_id="t1", name="write", result=ToolResult(ok=True, content="wrote"))


def refused() -> ToolEnd:
    return ToolEnd(
        tool_use_id="t1",
        name="write",
        result=ToolResult(ok=False, error="DENIED: the user declined this action"),
    )


def failed_for_another_reason() -> ToolEnd:
    """A gated call that ran and failed on its own merits — not a refusal."""
    return ToolEnd(
        tool_use_id="t1",
        name="write",
        result=ToolResult(ok=False, error="notes.txt has not been read in this session"),
    )


async def streamed(*events: Event) -> AsyncIterator[Event]:
    for event in events:
        yield event


DONE = TurnEnd(turn_index=0, state=TurnState.DONE, stop_reason="no_tool_calls")


# --------------------------------------------------------------------------- #
# The tracker itself
# --------------------------------------------------------------------------- #


def test_an_approval_that_was_granted_is_not_a_refusal() -> None:
    tracker = ApprovalTracker()
    for event in (TurnStart(turn_index=0), GATED, granted(), DONE):
        tracker.observe(event)
    assert tracker.resolve() == ()


def test_an_approval_that_was_refused_is_a_refusal() -> None:
    tracker = ApprovalTracker()
    for event in (TurnStart(turn_index=0), GATED, refused(), DONE):
        tracker.observe(event)
    assert tracker.resolve() == (GATED,)


def test_a_gated_call_that_ran_and_failed_on_its_merits_is_not_a_refusal() -> None:
    """Only the loop's own ``DENIED:`` prefix means the gate said no.

    A write that was approved and then rejected by the read-before-write guard is a
    tool failure the model can act on, not a permission outcome — and it must not turn
    into "the user declined", which would send a script looking for a human.
    """
    tracker = ApprovalTracker()
    for event in (GATED, failed_for_another_reason(), DONE):
        tracker.observe(event)
    assert tracker.resolve() == ()


def test_a_request_that_never_got_an_answer_counts_as_unresolved() -> None:
    """A stream that stopped mid-prompt did not do the work; exiting 0 would lie."""
    tracker = ApprovalTracker()
    tracker.observe(GATED)
    assert tracker.resolve() == (GATED,)


def test_each_request_is_resolved_by_its_own_tool_end() -> None:
    """Ids are how a granted call and a refused one are told apart in one turn."""
    second = ApprovalRequest(
        tool_use_id="t2", name="bash", danger_level=DangerLevel.DESTRUCTIVE, rendered="rm -rf build"
    )
    tracker = ApprovalTracker()
    for event in (
        GATED,
        second,
        granted(),
        ToolEnd(
            tool_use_id="t2",
            name="bash",
            result=ToolResult(ok=False, error="DENIED: not allowed here"),
        ),
        DONE,
    ):
        tracker.observe(event)
    assert tracker.resolve() == (second,)


# --------------------------------------------------------------------------- #
# The exit code, end to end through the headless path
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("outcome", "expected", "why"),
    [
        (granted(), EXIT_OK, "an approved gated call is an ordinary successful run"),
        (refused(), EXIT_NEEDS_APPROVAL, "a refusal is what exit 2 exists to report"),
        (failed_for_another_reason(), EXIT_OK, "a tool failure is not a permission outcome"),
    ],
    ids=["granted", "refused", "failed-otherwise"],
)
async def test_the_exit_code_reports_refusal_not_merely_asking(
    outcome: ToolEnd, expected: int, why: str
) -> None:
    result = await run_headless(
        streamed(TurnStart(turn_index=0), GATED, outcome, DONE),
        output_format=OutputFormat.TEXT,
        write=lambda _: None,
        write_error=lambda _: None,
    )
    assert result.exit_code == expected, why


async def test_a_granted_call_prints_no_denial_notice() -> None:
    """The notice told users their successful write had been declined."""
    notices: list[str] = []
    await run_headless(
        streamed(TurnStart(turn_index=0), GATED, granted(), DONE),
        output_format=OutputFormat.TEXT,
        write=lambda _: None,
        write_error=notices.append,
    )
    assert notices == []


async def test_a_refused_call_still_prints_one() -> None:
    notices: list[str] = []
    await run_headless(
        streamed(TurnStart(turn_index=0), GATED, refused(), DONE),
        output_format=OutputFormat.TEXT,
        write=lambda _: None,
        write_error=notices.append,
    )
    assert len(notices) == 1
    assert "write" in notices[0]


async def test_an_error_still_outranks_an_approval_outcome() -> None:
    """Precedence is unchanged: a broken run is 1 even if something was refused."""
    result = await run_headless(
        streamed(
            TurnStart(turn_index=0),
            GATED,
            refused(),
            Error(message="provider exploded", kind="provider"),
            TurnEnd(turn_index=0, state=TurnState.ERROR, stop_reason="error"),
        ),
        output_format=OutputFormat.TEXT,
        write=lambda _: None,
        write_error=lambda _: None,
    )
    assert result.exit_code == EXIT_ERROR


async def test_the_json_record_lists_only_real_refusals() -> None:
    """`approvals_denied` is consumed by scripts; a granted write must not appear."""
    result = await run_headless(
        streamed(TurnStart(turn_index=0), GATED, granted(), DONE),
        output_format=OutputFormat.TEXT,
        write=lambda _: None,
        write_error=lambda _: None,
    )
    assert result.approvals == ()
