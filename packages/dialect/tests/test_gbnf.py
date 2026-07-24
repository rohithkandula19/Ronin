"""Grammar lock: the GBNF text, and its pure-Python language twin `conforms`.

Offline proof strategy (no llama.cpp here): (1) `conforms` — the same language
the sampler enforces — rejects every malformed shape and passes every canonical
render; (2) the cli wiring test (test_embedded_provider) proves the grammar is
actually handed to the backend. Sampler-side enforcement itself runs wherever
llama-cpp-python is installed."""
from __future__ import annotations

import pytest

from ronin_dialect import render_tool_call
from ronin_dialect.gbnf import conforms, grammar_for_tools

NAMES = ["read_file", "edit_file", "run_command", "run_background",
         "stop_background", "background_logs"]


def test_grammar_text_structure():
    g = grammar_for_tools(NAMES)
    assert g.splitlines()[1].startswith("root")
    assert '"<tool_call>"' in g and '"</tool_call>"' in g
    assert '"\\"read_file\\""' in g          # locked name alternation
    assert "rj-object" in g                   # JSON-typed arguments rule
    # name-first key order is baked into the body rule definition
    body_rule = next(l for l in g.splitlines() if l.startswith("body"))
    assert body_rule.index('\\"name\\"') < body_rule.index('\\"arguments\\"')


def test_grammar_rejects_non_dialect_tool_names():
    with pytest.raises(ValueError):
        grammar_for_tools(["Bad-Name"])


def test_valid_canonical_render_passes_untouched():
    for name in NAMES[:3]:
        block = render_tool_call(name, {"path": "a.py", "lines": 5})
        assert conforms(block, NAMES)
        assert conforms(f"I'll read it first.\n{block}", NAMES)


def test_pure_prose_passes():
    assert conforms("The suite is green; nothing else to do.", NAMES)


def test_malformed_generations_are_rejected():
    bad = [
        '<tool_call>{not json}</tool_call>',                                   # broken JSON
        '<tool_call>{"arguments": {}, "name": "read_file"}</tool_call>',       # wrong key order
        '<tool_call>{"name": "read_file", "arguments": "{\\"p\\": 1}"}</tool_call>',  # string args
        '<tool_call>{"name": "teleport", "arguments": {}}</tool_call>',        # unknown name
        '<tool_call>{"name": "read_file", "arguments": {}}',                   # unclosed block
        'prose with a stray < bracket',                                        # documented constraint
    ]
    for text in bad:
        assert not conforms(text, NAMES), text


def test_open_name_mode_accepts_any_lower_snake():
    block = '<tool_call>\n{"name": "anything_goes", "arguments": {}}\n</tool_call>'
    assert conforms(block)                    # no name lock
    assert not conforms(block, NAMES)         # locked: rejected
