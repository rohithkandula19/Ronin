"""Golden-file guarantees for the canonical tool-call dialect.

These tests are the structural fix for valid_tool_json drift: if the renderer and
either consumer (runtime parser, eval extractor) ever disagree, CI fails offline —
no GPU, no model."""
from __future__ import annotations

import json

import pytest

from ronin_dialect import (
    CALL_SHAPE_RE,
    ParsedCall,
    extract_call_names,
    parse_tool_calls,
    render_tool_call,
    render_tool_call_message,
)

GOLDEN = [
    ("read_file", {"path": "src/config.py"}),
    ("edit_file", {"path": "config.py", "edits": [
        {"old_string": "DEBUG =", "new_string": "VERBOSE ="},
        {"old_string": "TIMEOUT = 30", "new_string": "TIMEOUT = 60"},
    ]}),
    ("run_background", {"command": "npm run dev"}),
    ("stop_background", {"task_id": "bg_1"}),
    ("run_command", {"command": "pytest -q", "timeout": 120}),
]


def test_render_parse_roundtrip_identical():
    for name, args in GOLDEN:
        clean, calls = parse_tool_calls(render_tool_call(name, args))
        assert clean == ""                      # nothing left over
        assert calls == [ParsedCall(name=name, arguments=args)]


def test_eval_extractor_accepts_every_canonical_render():
    # The eval metric's exact extraction path must credit the canonical format.
    for name, args in GOLDEN:
        assert extract_call_names(render_tool_call(name, args)) == [name]


def test_multi_call_message_roundtrip():
    msg = render_tool_call_message(
        [{"name": n, "arguments": a} for n, a in GOLDEN])
    clean, calls = parse_tool_calls(msg)
    assert clean == ""
    assert [(c.name, c.arguments) for c in calls] == GOLDEN
    assert extract_call_names(msg) == [n for n, _ in GOLDEN]


def test_legacy_string_arguments_still_parse():
    # 560/560 existing corpus rows carry arguments as a JSON-encoded string —
    # the parser must keep accepting them (double-decode), forever.
    legacy = ('<tool_call>\n{"name": "read_file", "arguments": '
              '"{\\"path\\": \\"src/config.py\\"}"}\n</tool_call>')
    clean, calls = parse_tool_calls(legacy)
    assert clean == ""
    assert calls == [ParsedCall("read_file", {"path": "src/config.py"})]
    assert extract_call_names(legacy) == ["read_file"]


def test_canonical_arguments_are_an_object_not_a_string():
    # The whole point of the canonical form: obey the template's own instruction.
    body = json.loads(
        render_tool_call("read_file", {"path": "x"})
        .removeprefix("<tool_call>\n").removesuffix("\n</tool_call>"))
    assert isinstance(body["arguments"], dict)


def test_malformed_blocks_stay_visible_never_repaired():
    bad = "<tool_call>{not json}</tool_call> and prose"
    clean, calls = parse_tool_calls(bad)
    assert calls == []
    assert "<tool_call>{not json}</tool_call>" in clean
    # missing/empty name, non-dict arguments: kept verbatim too
    for body in ('{"arguments": {}}', '{"name": "", "arguments": {}}',
                 '{"name": "x", "arguments": 3}'):
        clean, calls = parse_tool_calls(f"<tool_call>{body}</tool_call>")
        assert calls == [] and body in clean


def test_call_shape_regex_rejects_prose_quoted_bare_json():
    # Runtime parity: JSON quoted in prose (outside the wrapper) is never a call.
    prose = 'I would run {"name": "run_command", "arguments": {"command": "rm -rf ~"}} but I refuse.'
    assert extract_call_names(prose) == []
    _, calls = parse_tool_calls(prose)
    assert calls == []


def test_deterministic_rendering():
    a = render_tool_call("run_command", {"timeout": 5, "command": "ls"})
    b = render_tool_call("run_command", {"command": "ls", "timeout": 5})
    assert a == b  # sort_keys: same call → same bytes, whatever the dict order


def test_render_rejects_garbage():
    with pytest.raises(ValueError):
        render_tool_call("", {})
    with pytest.raises(ValueError):
        render_tool_call("x", "not a dict")  # type: ignore[arg-type]
