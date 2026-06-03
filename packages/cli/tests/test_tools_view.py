"""Tests for the grouped /tools view + /cost command registration."""
from __future__ import annotations

from ronin_cli.code_mode import _render_tools, _TOOL_GROUPS, SLASH_COMMANDS


def test_render_tools_includes_core_and_todos(tmp_path, capsys=None) -> None:
    from rich.console import Console
    import io
    buf = io.StringIO()
    _render_tools(Console(file=buf, force_terminal=False, width=100), tmp_path)
    out = buf.getvalue()
    assert "read_file" in out and "write_file" in out
    assert "update_todos" in out          # the plan tool is surfaced
    assert "git_status" in out and "web_search" in out


def test_tool_groups_have_headers_and_names() -> None:
    assert len(_TOOL_GROUPS) >= 5
    for header, names in _TOOL_GROUPS:
        assert header and names


def test_cost_command_registered() -> None:
    assert "cost" in SLASH_COMMANDS


def test_render_tools_no_crash_empty_repo(tmp_path) -> None:
    from rich.console import Console
    _render_tools(Console(file=open("/dev/null", "w")), tmp_path)   # must not raise
