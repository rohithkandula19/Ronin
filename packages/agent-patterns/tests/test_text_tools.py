"""Tests for recovering tool calls that open models emit as text."""
from __future__ import annotations

from ronin_agent_patterns.providers.text_tools import extract_text_tool_calls

TOOLS = ["write_file", "read_file", "run_command"]


def test_tag_format() -> None:
    text = 'Sure.\n<tool_call>{"name": "write_file", "arguments": {"path": "a.py", "content": "x"}}</tool_call>'
    calls = extract_text_tool_calls(text, TOOLS)
    assert len(calls) == 1
    assert calls[0].name == "write_file"
    assert calls[0].arguments == {"path": "a.py", "content": "x"}


def test_fenced_json() -> None:
    text = 'I will run it:\n```json\n{"name": "run_command", "arguments": {"command": "ls"}}\n```'
    calls = extract_text_tool_calls(text, TOOLS)
    assert len(calls) == 1 and calls[0].name == "run_command"
    assert calls[0].arguments["command"] == "ls"


def test_bare_json_in_prose() -> None:
    text = 'Let me read it. {"tool": "read_file", "parameters": {"path": "x.py"}} done.'
    calls = extract_text_tool_calls(text, TOOLS)
    assert len(calls) == 1 and calls[0].name == "read_file"
    assert calls[0].arguments == {"path": "x.py"}


def test_whole_text_is_json() -> None:
    text = '{"name": "write_file", "arguments": {"path": "a", "content": "b"}}'
    assert len(extract_text_tool_calls(text, TOOLS)) == 1


def test_function_nested_shape() -> None:
    text = '<tool_call>{"function": {"name": "write_file", "arguments": "{\\"path\\": \\"z\\"}"}}</tool_call>'
    calls = extract_text_tool_calls(text, TOOLS)
    assert calls and calls[0].name == "write_file" and calls[0].arguments == {"path": "z"}


def test_unknown_tool_name_ignored() -> None:
    # ordinary JSON in an answer must NOT be mistaken for a tool call
    text = '{"name": "not_a_tool", "arguments": {"x": 1}}'
    assert extract_text_tool_calls(text, TOOLS) == []


def test_plain_prose_no_calls() -> None:
    assert extract_text_tool_calls("Here is how the auth module works…", TOOLS) == []


def test_multiple_calls_deduped() -> None:
    text = ('{"name": "read_file", "arguments": {"path": "a"}}\n'
            '{"name": "read_file", "arguments": {"path": "a"}}\n'   # dup
            '{"name": "read_file", "arguments": {"path": "b"}}')
    calls = extract_text_tool_calls(text, TOOLS)
    assert len(calls) == 2
    assert {c.arguments["path"] for c in calls} == {"a", "b"}


def test_synthetic_ids() -> None:
    text = '{"name": "read_file", "arguments": {"path": "a"}}'
    assert extract_text_tool_calls(text, TOOLS)[0].id == "text-0"


def test_empty_and_no_tools() -> None:
    assert extract_text_tool_calls("", TOOLS) == []
    assert extract_text_tool_calls('{"name":"write_file"}', []) == []


def test_action_input_shape() -> None:
    # ReAct-style {"action": ..., "action_input": ...}
    text = '{"action": "run_command", "action_input": {"command": "pytest"}}'
    calls = extract_text_tool_calls(text, TOOLS)
    assert calls and calls[0].name == "run_command"
