"""The --god-mode destructive floor: catastrophic shell commands are NEVER
silently auto-approved, even under yolo. Typed confirmation required."""
from __future__ import annotations

import io
from pathlib import Path

import pytest
from rich.console import Console

from ronin_cli.code_mode import _selective_gate
from ronin_cli.ui_cards import render_destructive_block, safer_alternative


def _console():
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, width=100), buf


def _gate(console, *, yolo: bool, root: Path):
    return _selective_gate(console, yolo, root)


# --- the floor holds even under yolo -----------------------------------------

def test_yolo_does_not_auto_approve_destructive(monkeypatch, tmp_path) -> None:
    console, buf = _console()
    gate = _gate(console, yolo=True, root=tmp_path)
    monkeypatch.setattr("builtins.input", lambda: "no thanks")   # wrong phrase
    result = gate("run_command", {"command": "rm -rf /"})
    assert result != True  # noqa: E712 — must be a denial string, not True
    assert isinstance(result, str) and "not confirmed" in result
    assert "Destructive" in buf.getvalue() or "destructive" in buf.getvalue()


def test_typed_phrase_confirms_destructive(monkeypatch, tmp_path) -> None:
    console, _ = _console()
    gate = _gate(console, yolo=True, root=tmp_path)
    monkeypatch.setattr("builtins.input", lambda: "run destructive")
    assert gate("run_command", {"command": "git push --force"}) is True


def test_non_destructive_under_yolo_still_auto_approves(tmp_path) -> None:
    console, _ = _console()
    gate = _gate(console, yolo=True, root=tmp_path)
    # a normal command under yolo is unchanged — auto-approved, no prompt
    assert gate("run_command", {"command": "git status --short"}) is True


def test_destructive_denied_without_console(tmp_path) -> None:
    # headless (console=None): a destructive command can NEVER run — never silent
    gate = _gate(None, yolo=True, root=tmp_path)
    result = gate("run_command", {"command": "rm -rf ~/data"})
    assert result is not True
    assert isinstance(result, str) and "refused" in result


def test_eof_cancels_destructive(monkeypatch, tmp_path) -> None:
    console, _ = _console()
    gate = _gate(console, yolo=True, root=tmp_path)
    def _raise():
        raise EOFError
    monkeypatch.setattr("builtins.input", _raise)
    result = gate("run_command", {"command": "mkfs.ext4 /dev/sda"})
    assert result is not True and "cancelled" in result


def test_floor_covers_the_marker_set(monkeypatch, tmp_path) -> None:
    console, _ = _console()
    gate = _gate(console, yolo=True, root=tmp_path)
    monkeypatch.setattr("builtins.input", lambda: "cancel")
    for cmd in ("rm -rf .", "drop table users", "git push -f", "dd if=/dev/zero of=/dev/sda",
                ":(){ :|:& };:"):
        assert gate("run_command", {"command": cmd}) is not True, cmd


# --- the card + safer alternatives -------------------------------------------

def test_safer_alternative_hints() -> None:
    assert "force-with-lease" in safer_alternative("git push --force origin main")
    assert "checkpoint" in safer_alternative("rm -rf build/")
    assert "back up" in safer_alternative("drop table users").lower()
    assert "erases a disk" in safer_alternative("mkfs.ext4 /dev/sdb")
    assert safer_alternative("ls -la") == ""


def test_destructive_block_card_shows_command_and_safer() -> None:
    console, buf = _console()
    render_destructive_block(console, "git push --force", ".")
    out = buf.getvalue()
    assert "git push --force" in out
    assert "blocked" in out.lower()
    assert "force-with-lease" in out  # safer alternative surfaced


def test_destructive_block_card_no_color(monkeypatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    console, buf = _console()
    render_destructive_block(console, "rm -rf /tmp/x", ".")
    out = buf.getvalue()
    assert "rm -rf /tmp/x" in out
    # plain-text card: no rich box-drawing borders
    assert "─" not in out and "╭" not in out
