"""The headless path: the JSON schema contract, exit codes, and the deny policy."""

from __future__ import annotations

import json
from typing import Any

import pytest
from ui_harness import (
    APPROVAL,
    approval_turn,
    clean_turn,
    errored_turn,
    every_event,
    happy_turn,
    interrupted_turn,
    stream,
)

from ronin.core.protocols import Policy
from ronin.core.types import (
    EVENT_TYPES,
    ApprovalRequest,
    Budget,
    DangerLevel,
    Error,
    Event,
    ToolSpec,
    ToolStart,
    ToolUse,
)
from ronin.ui.headless import (
    DENIAL_NOTICE,
    EVENT_TYPE_NAMES,
    EXIT_ERROR,
    EXIT_NEEDS_APPROVAL,
    EXIT_OK,
    NO_TURN_END,
    RESULT_TYPE,
    SCHEMA,
    HeadlessPolicy,
    OutputFormat,
    event_to_json,
    exit_code_for,
    run_headless,
)
from ronin.ui.reduce import ViewState, reduce_all


class Sink:
    """Captures what the runner writes, so no test touches a real stream."""

    def __init__(self) -> None:
        self.out: list[str] = []
        self.err: list[str] = []
        self.flushes = 0

    def write(self, text: str) -> None:
        self.out.append(text)

    def write_error(self, text: str) -> None:
        self.err.append(text)

    def flush(self) -> None:
        self.flushes += 1

    def records(self) -> list[dict[str, Any]]:
        parsed: list[dict[str, Any]] = []
        for line in self.out:
            for piece in line.splitlines():
                if piece:
                    parsed.append(json.loads(piece))
        return parsed


async def _run(
    events: tuple[Event, ...], output_format: OutputFormat = OutputFormat.STREAM_JSON
) -> tuple[Sink, int]:
    sink = Sink()
    result = await run_headless(
        stream(events),
        output_format=output_format,
        write=sink.write,
        write_error=sink.write_error,
        flush=sink.flush,
    )
    return sink, result.exit_code


# --------------------------------------------------------------------------- #
# Schema contract
# --------------------------------------------------------------------------- #


def test_every_member_of_the_event_union_has_a_json_type() -> None:
    assert set(EVENT_TYPE_NAMES) == set(EVENT_TYPES)


def test_every_json_type_is_documented_in_the_module_docstring() -> None:
    from ronin.ui import headless

    doc = headless.__doc__ or ""
    for name, fields in SCHEMA.items():
        assert f"``{name}``" in doc, f"{name} is emitted but undocumented"
        for field in fields:
            assert f"``{field}``" in doc, f"{name}.{field} is emitted but undocumented"


def test_every_event_serializes_to_exactly_its_documented_fields() -> None:
    for event in every_event():
        payload = event_to_json(event)
        name = EVENT_TYPE_NAMES[type(event)]
        assert payload["type"] == name
        assert set(payload) - {"type"} == set(SCHEMA[name])


async def test_the_result_record_carries_exactly_its_documented_fields() -> None:
    sink, _ = await _run(happy_turn())
    result = sink.records()[-1]
    assert result["type"] == RESULT_TYPE
    assert set(result) - {"type"} == set(SCHEMA[RESULT_TYPE])


async def test_stream_json_emits_one_object_per_event_plus_a_result() -> None:
    events = happy_turn()
    sink, _ = await _run(events)
    records = sink.records()
    assert len(records) == len(events) + 1
    assert [record["type"] for record in records[: len(events)]] == [
        EVENT_TYPE_NAMES[type(event)] for event in events
    ]


async def test_every_line_is_flushed_so_a_pipe_sees_progress() -> None:
    events = happy_turn()
    sink, _ = await _run(events)
    # one flush per event, plus one for the closing result record
    assert sink.flushes == len(events) + 1


async def test_a_line_is_one_json_object_terminated_by_a_newline() -> None:
    sink, _ = await _run(happy_turn())
    for line in sink.out:
        assert line.endswith("\n")
        assert json.loads(line)


def test_arguments_that_are_not_json_are_coerced_rather_than_crashing() -> None:
    payload = event_to_json(
        ToolStart(tool_use_id="t", name="X", arguments={"obj": object(), "n": {1: [2, 3]}})
    )
    assert isinstance(payload["arguments"]["obj"], str)
    assert payload["arguments"]["n"] == {"1": [2, 3]}


def test_the_approval_record_keeps_the_rendered_text_and_the_danger_level() -> None:
    payload = event_to_json(APPROVAL)
    assert payload["rendered"] == APPROVAL.rendered
    assert payload["danger_level"] == "destructive"
    assert payload["danger_rank"] == int(DangerLevel.DESTRUCTIVE)


async def test_a_stream_reset_does_not_duplicate_the_headless_answer() -> None:
    sink, _ = await _run(happy_turn())
    result = sink.records()[-1]
    assert result["text"] == "Reading src/main.py now."
    assert result["resets"] == 1


# --------------------------------------------------------------------------- #
# Exit codes
# --------------------------------------------------------------------------- #


async def test_a_clean_turn_exits_zero() -> None:
    _, code = await _run(clean_turn())
    assert code == EXIT_OK


async def test_an_errored_turn_exits_one() -> None:
    sink, code = await _run(errored_turn())
    assert code == EXIT_ERROR
    assert any("provider returned 500" in line for line in sink.err)


async def test_an_interrupted_turn_exits_one() -> None:
    _, code = await _run(interrupted_turn())
    assert code == EXIT_ERROR


async def test_an_approval_request_exits_two() -> None:
    sink, code = await _run(approval_turn())
    assert code == EXIT_NEEDS_APPROVAL
    assert sink.records()[-1]["approvals_denied"][0]["rendered"] == APPROVAL.rendered


async def test_an_error_outranks_an_approval() -> None:
    events = (*approval_turn(), Error(message="also broke", kind="tool"))
    _, code = await _run(events)
    assert code == EXIT_ERROR


async def test_a_stream_that_never_ends_a_turn_is_a_failure() -> None:
    sink, code = await _run(happy_turn()[:3])
    assert code == EXIT_ERROR
    assert any(NO_TURN_END in line for line in sink.err)
    assert sink.records()[-1]["stop_reason"] == NO_TURN_END


@pytest.mark.parametrize(
    ("events", "expected"),
    [
        (clean_turn(), EXIT_OK),
        (errored_turn(), EXIT_ERROR),
        (interrupted_turn(), EXIT_ERROR),
        (approval_turn(), EXIT_NEEDS_APPROVAL),
    ],
)
def test_the_exit_code_is_a_pure_function_of_the_stream(
    events: tuple[Event, ...], expected: int
) -> None:
    state = reduce_all(events)
    approvals = tuple(event for event in events if isinstance(event, ApprovalRequest))
    assert exit_code_for(state, approvals) == expected


def test_an_empty_stream_is_not_success() -> None:
    assert exit_code_for(ViewState(), ()) == EXIT_ERROR


# --------------------------------------------------------------------------- #
# Formats
# --------------------------------------------------------------------------- #


async def test_text_format_prints_only_the_final_answer() -> None:
    sink, code = await _run(happy_turn(), OutputFormat.TEXT)
    assert "".join(sink.out) == "Reading src/main.py now.\n"
    assert code == EXIT_OK


async def test_text_format_keeps_notices_off_stdout() -> None:
    sink, code = await _run(approval_turn(), OutputFormat.TEXT)
    assert "DENIED" not in "".join(sink.out)
    assert any("DENIED" in line for line in sink.err)
    assert code == EXIT_NEEDS_APPROVAL


async def test_json_format_emits_exactly_one_object_at_the_end() -> None:
    sink, _ = await _run(happy_turn(), OutputFormat.JSON)
    records = sink.records()
    assert len(records) == 1
    assert records[0]["type"] == RESULT_TYPE


async def test_the_denial_notice_names_the_tool_and_what_it_would_have_run() -> None:
    sink, _ = await _run(approval_turn())
    notice = "".join(sink.err)
    assert DENIAL_NOTICE.format(name="Bash", rendered=APPROVAL.rendered) in notice


# --------------------------------------------------------------------------- #
# The policy
# --------------------------------------------------------------------------- #


async def test_the_headless_policy_denies_every_approval() -> None:
    policy = HeadlessPolicy()
    spec = ToolSpec(
        name="Bash",
        description="run a command",
        danger_level=DangerLevel.DESTRUCTIVE,
        requires_approval=True,
    )
    use = ToolUse(id="t1", name="Bash", arguments={"command": "rm -rf /"})
    decision = await policy.approve(spec, use, rendered="rm -rf /")
    assert decision.approved is False
    assert decision.reason
    assert policy.denials == [("Bash", "rm -rf /")]


async def test_the_headless_policy_denies_a_read_only_tool_too() -> None:
    policy = HeadlessPolicy()
    spec = ToolSpec(name="Read", description="read a file")
    decision = await policy.approve(spec, ToolUse(id="t", name="Read"), rendered="Read({})")
    assert decision.approved is False


def test_the_headless_policy_is_a_policy_the_loop_accepts() -> None:
    assert isinstance(HeadlessPolicy(), Policy)


@pytest.mark.parametrize(
    ("limits", "spend", "expected"),
    [
        (Budget(), Budget(spent_tokens=10**9), None),
        (Budget(max_tokens=100), Budget(spent_tokens=100), "token_budget"),
        (Budget(max_tokens=100), Budget(spent_tokens=99), None),
        (Budget(max_usd=1.0), Budget(spent_usd=1.5), "cost_budget"),
        (Budget(max_wall_seconds=5.0), Budget(elapsed_seconds=5.0), "wall_budget"),
    ],
)
def test_the_headless_policy_enforces_its_budget(
    limits: Budget, spend: Budget, expected: str | None
) -> None:
    assert HeadlessPolicy(budget=limits).check_budget(spend) == expected


def test_cancellation_is_explicit_and_starts_off() -> None:
    policy = HeadlessPolicy()
    assert policy.cancelled() is False
    policy.cancel()
    assert policy.cancelled() is True
