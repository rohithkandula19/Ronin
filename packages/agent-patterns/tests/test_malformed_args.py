"""Regression for F9: malformed tool-call JSON was silently coerced to {}.

That made the tool run argument-less and the recovery path mis-coach the model
about argument *names*. Now the raw string is preserved behind a sentinel and
the executor returns a distinct 'not valid JSON' error.
"""
from ronin_agent_patterns.types import MALFORMED_ARGS_KEY, Tool
from ronin_agent_patterns.base import execute_tool_call
from ronin_agent_patterns.providers.openai_compat import OpenAICompatProvider


def test_parse_args_preserves_malformed_json():
    out = OpenAICompatProvider._parse_args('{"path": "a.py", "content": ')  # truncated
    assert MALFORMED_ARGS_KEY in out
    assert out[MALFORMED_ARGS_KEY].startswith('{"path"')


def test_parse_args_still_parses_valid_json():
    assert OpenAICompatProvider._parse_args('{"x": 1}') == {"x": 1}


def test_executor_reports_malformed_json_distinctly():
    tool = Tool(name="write_file", description="write",
                input_schema={"type": "object",
                              "properties": {"path": {}, "content": {}},
                              "required": ["path", "content"]},
                handler=lambda path, content: "ok")
    result, is_error = execute_tool_call(tool, {MALFORMED_ARGS_KEY: '{"path": '})
    assert is_error is True
    assert "not valid JSON" in result
    assert "write_file" in result
    # It must NOT be the wrong-argument-name coaching message.
    assert "Retry with exactly those names" not in result
