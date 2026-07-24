"""The eval metric and the canonical dialect must stay in lockstep — offline."""
from __future__ import annotations

from ronin_dialect import render_tool_call
from ronin_training.eval_runner import extract_all_call_names, extract_tool_names


def test_eval_metric_accepts_canonical_render():
    block = render_tool_call("read_file", {"path": "src/config.py"})
    assert extract_all_call_names(block) == ["read_file"]
    # and the registry-gated variant credits real tools
    assert extract_tool_names(block) == ["read_file"]


def test_eval_metric_accepts_legacy_string_arguments():
    legacy = ('<tool_call>\n{"name": "edit_file", "arguments": '
              '"{\\"path\\": \\"a.py\\"}"}\n</tool_call>')
    assert extract_all_call_names(legacy) == ["edit_file"]


def test_prose_quoted_json_still_earns_nothing():
    prose = 'the call would be {"name": "run_command", "arguments": {}} but no.'
    assert extract_all_call_names(prose) == []
