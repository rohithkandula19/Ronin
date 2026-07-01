"""Premium approval cards (Phase 5) — shell + edit, risk labels, bracket-safety."""
from __future__ import annotations

import io
import re

from rich.console import Console

from ronin_cli.ui_cards import command_risk, render_edit_approval, render_shell_approval

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _out(fn, *a, no_color=False, monkeypatch=None, **k):
    if no_color and monkeypatch is not None:
        monkeypatch.setenv("NO_COLOR", "1")
    buf = io.StringIO()
    fn(Console(file=buf, force_terminal=not no_color, width=90), *a, **k)
    return _ANSI.sub("", buf.getvalue())


def test_command_risk_labels() -> None:
    assert command_risk("git status --short") == "read-only"
    assert command_risk("ls -la") == "read-only"
    assert command_risk("npm install") == "runs a command"
    assert "destructive" in command_risk("rm -rf build")


def test_shell_card_shows_command_dir_risk() -> None:
    out = _out(render_shell_approval, "git status --short", "/Users/x/ronin")
    assert "Approval required: shell command" in out
    assert "git status --short" in out
    assert "/Users/x/ronin" in out
    assert "read-only" in out


def test_shell_card_bracket_command_is_safe() -> None:
    # a command with [brackets] must render literally, not crash/restyle
    out = _out(render_shell_approval, "grep -E '[0-9]+' file.txt", ".")
    assert "[0-9]+" in out


def test_edit_card_shows_file_and_change() -> None:
    out = _out(render_edit_approval, "packages/cli/status.py", added=42, removed=8,
               reason="improve renderer")
    assert "Approval required: file edit" in out
    assert "packages/cli/status.py" in out
    assert "+42 -8" in out
    assert "workspace mutation" in out
    assert "improve renderer" in out


def test_cards_no_color_plain(monkeypatch) -> None:
    out = _out(render_shell_approval, "make test", ".", no_color=True, monkeypatch=monkeypatch)
    assert "make test" in out and "shell command" in out
    assert "╭" not in out and "│" not in out   # plain text, no box
