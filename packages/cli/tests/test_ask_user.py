"""Tests for the clarifying-questions (ask_user) tool."""
from __future__ import annotations

from rich.console import Console

from ronin_cli.code_mode import build_ask_user_tool


def _tool(answer: str):
    """Build an ask_user tool whose 'human' always types ``answer``."""
    console = Console(file=open("/dev/null", "w"))
    return build_ask_user_tool(console, ask_fn=lambda _prompt: answer)


def test_ask_user_returns_free_text() -> None:
    tool = _tool("use postgres")
    assert tool.handler(question="which database?") == "use postgres"


def test_ask_user_resolves_numbered_option() -> None:
    tool = _tool("2")
    out = tool.handler(question="which framework?", options=["flask", "fastapi", "django"])
    assert out == "fastapi"  # picked option 2 by number


def test_ask_user_out_of_range_number_is_literal() -> None:
    tool = _tool("9")
    out = tool.handler(question="pick", options=["a", "b"])
    assert out == "9"  # not a valid option index → returned as-is


def test_ask_user_empty_answer_falls_back() -> None:
    tool = _tool("   ")
    out = tool.handler(question="anything?")
    assert "proceed with your best judgment" in out


def test_ask_user_handles_eof() -> None:
    def boom(_prompt):
        raise EOFError

    console = Console(file=open("/dev/null", "w"))
    tool = build_ask_user_tool(console, ask_fn=boom)
    out = tool.handler(question="?")
    assert "user is unavailable" in out


def test_ask_user_schema_shape() -> None:
    tool = _tool("x")
    assert tool.name == "ask_user"
    assert "question" in tool.input_schema["required"]
    assert tool.input_schema["properties"]["options"]["type"] == "array"
