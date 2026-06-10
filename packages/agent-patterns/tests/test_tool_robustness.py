"""Tests for robust tool execution — absorbing the argument mistakes weak models
make instead of crashing the turn."""
from __future__ import annotations

from ronin_agent_patterns.base import _adapt_args, execute_tool_call
from ronin_agent_patterns.types import Tool


def _tool(handler, schema=None) -> Tool:
    return Tool(name="t", description="d",
                input_schema=schema or {"type": "object", "properties": {}}, handler=handler)


# ---------- _adapt_args ----------


def test_adapt_remaps_alias_to_real_param() -> None:
    def h(directory="."):
        return directory
    adapted, dropped = _adapt_args(h, {"path": "src"})
    assert adapted == {"directory": "src"}      # path → directory (same family)
    assert dropped == []


def test_adapt_passes_through_var_kwargs() -> None:
    def h(**kw):
        return kw
    adapted, dropped = _adapt_args(h, {"anything": 1, "else": 2})
    assert adapted == {"anything": 1, "else": 2}
    assert dropped == []


def test_adapt_drops_truly_unknown() -> None:
    def h(query, directory="."):
        return query
    adapted, dropped = _adapt_args(h, {"query": "x", "bogus": 9})
    assert adapted == {"query": "x"}
    assert dropped == ["bogus"]


def test_adapt_does_not_overwrite_filled_param() -> None:
    def h(directory="."):
        return directory
    # both directory and its alias supplied → keep the real one, drop the alias
    adapted, dropped = _adapt_args(h, {"directory": "real", "path": "alias"})
    assert adapted == {"directory": "real"}
    assert dropped == ["path"]


# ---------- execute_tool_call ----------


def test_execute_remaps_and_succeeds() -> None:
    def search(query, directory="."):
        return f"searched {query} in {directory}"
    out, is_err = execute_tool_call(
        _tool(search, {"type": "object", "properties": {"query": {}, "directory": {}},
                       "required": ["query"]}),
        {"query": "foo", "path": "src"},   # model used path, not directory
    )
    assert is_err is False
    assert out == "searched foo in src"


def test_execute_missing_required_gives_coaching_error() -> None:
    def needs(query):
        return query
    out, is_err = execute_tool_call(
        _tool(needs, {"type": "object", "properties": {"query": {}}, "required": ["query"]}),
        {"wrongname": "x"},
    )
    assert is_err is True
    assert "Expected arguments: query" in out
    assert "Retry" in out


def test_execute_surfaces_handler_value_error() -> None:
    def boom(x):
        raise ValueError("bad path")
    out, is_err = execute_tool_call(
        _tool(boom, {"type": "object", "properties": {"x": {}}, "required": ["x"]}),
        {"x": 1},
    )
    assert is_err is True
    assert "ValueError: bad path" in out


def test_execute_serializes_nonstring_result() -> None:
    out, is_err = execute_tool_call(_tool(lambda: [1, 2, 3]), {})
    assert is_err is False
    assert out == "[1, 2, 3]"


def test_execute_drops_extra_kwarg_and_runs() -> None:
    def h(a, b=2):
        return a + b
    out, is_err = execute_tool_call(
        _tool(h, {"type": "object", "properties": {"a": {}, "b": {}}, "required": ["a"]}),
        {"a": 5, "totally_unknown": 99},
    )
    assert is_err is False
    assert out == "7"
