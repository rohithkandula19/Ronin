"""Regression for F8: the context-remaining gauge always read 100%.

``message_history`` holds ``ronin_agent_patterns.Message`` objects, not dicts.
The old gauge skipped every non-dict item, so it summed 0 tokens and pinned
"context left" at 100% no matter how large the conversation grew. These tests
assert the estimator counts Message content AND tool-call arguments.
"""
from __future__ import annotations

from ronin_agent_patterns import Message, ToolCall
from ronin_cli.code_mode import _history_token_estimate
from ronin_cli.config import RoninConfig
from ronin_cli.context_policy import resolve_context_policy


def test_empty_history_is_zero():
    assert _history_token_estimate([]) == 0
    assert _history_token_estimate(None) == 0


def test_counts_message_objects_not_just_dicts():
    # ~8000 chars of content -> ~2000 tokens; the old code returned 0 here.
    history = [
        Message(role="user", content="x" * 4000),
        Message(role="assistant", content="y" * 4000),
    ]
    est = _history_token_estimate(history)
    assert est >= 1800, est
    # And it must move the gauge off 100%.
    left = resolve_context_policy(RoninConfig(provider="anthropic")).remaining_percent(est)
    assert left < 100


def test_counts_tool_call_arguments():
    # write_file payloads live in tool_calls[].arguments, not content.
    big = "z" * 8000
    history = [
        Message(
            role="assistant",
            content="",
            tool_calls=[ToolCall(id="1", name="write_file",
                                 arguments={"path": "a.py", "content": big})],
        ),
    ]
    est = _history_token_estimate(history)
    assert est >= 1900, est


def test_tolerates_legacy_dict_messages():
    history = [{"role": "user", "content": "w" * 4000}]
    assert _history_token_estimate(history) >= 900
