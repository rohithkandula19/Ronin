"""The holdout tool-call validity hook — the number the SFT run is judged on."""

from __future__ import annotations

from ronin_training.sft.eval_hook import (
    GATE,
    ToolCall,
    ValidityReport,
    calls_from_rows,
    tool_validity,
)

_KNOWN = frozenset({"read", "write", "glob", "bash"})


def test_a_known_tool_with_object_args_is_valid() -> None:
    report = tool_validity([ToolCall("read", {"path": "a.py"})], known_tools=_KNOWN)
    assert report.rate == 1.0
    assert report.meets_gate()


def test_an_unknown_tool_or_string_args_is_invalid() -> None:
    calls = [
        ToolCall("read", {"path": "a.py"}),  # valid
        ToolCall("nonesuch", {"x": 1}),  # D1: unknown name
        ToolCall("write", '{"path": "a"}'),  # D2: string args, not an object
    ]
    report = tool_validity(calls, known_tools=_KNOWN)
    assert (report.valid, report.attempts) == (1, 3)
    assert report.rate is not None and report.rate < GATE
    assert not report.meets_gate()


def test_nothing_attempted_is_not_a_hundred_percent_pass() -> None:
    report = tool_validity([], known_tools=_KNOWN)
    assert report.rate is None
    assert not report.meets_gate()  # an empty result never meets the gate


def test_gate_is_ninety_seven_percent() -> None:
    assert GATE == 0.97
    # 96/100 is below; 97/100 is at the bar.
    assert not ValidityReport(valid=96, attempts=100).meets_gate()
    assert ValidityReport(valid=97, attempts=100).meets_gate()


def test_calls_are_extracted_from_the_assistant_turns_of_rows() -> None:
    rows = [
        {
            "messages": [
                {"role": "user", "content": "go"},
                {
                    "role": "assistant",
                    "tool_calls": [{"function": {"name": "glob", "arguments": {"pattern": "*"}}}],
                },
                {"role": "tool", "content": "a.py"},
                {"role": "assistant", "content": "done"},  # no call — not counted
            ]
        }
    ]
    calls = calls_from_rows(rows)
    assert [c.name for c in calls] == ["glob"]
    assert tool_validity(calls, known_tools=_KNOWN).rate == 1.0
