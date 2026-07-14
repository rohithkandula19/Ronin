"""The destructive floor must hold on EVERY gate path, not just the console one
(vNext discovery P1): before this, `ronin --tui --god-mode` (the gate_cb path)
auto-approved rm -rf / git reset --hard because only the console `_selective_gate`
applied the floor. Both gates now share `_is_floored_command`."""
from __future__ import annotations

import pytest

from ronin_cli.code_mode import _is_floored_command, _selective_gate


@pytest.mark.parametrize("name,cmd,floored", [
    ("run_command", "rm -rf /", True),
    ("run_command", "git reset --hard", True),
    ("run_command", "git clean -fdx", True),
    ("run_background", "git reset --hard HEAD~3", True),   # both command tools
    ("run_background", "rm -rf ~", True),
    ("run_command", "ls -la", False),
    ("run_command", "git status", False),
    ("run_background", "npm run dev", False),
    # The floor is PAYLOAD-based, not name-based (see test_floor_scope.py): a
    # tool's name proves nothing, since MCP servers and plugins choose their own.
    # So ANY tool handed an executable `command` that is destructive is floored —
    # deliberately fail-safe. (A realistic write_file carries path/content, which
    # are NOT executable keys and are never floored.)
    ("write_file", "rm -rf /", True),
    ("read_file", "anything", False),                      # not a destructive payload
])
def test_is_floored_command(name, cmd, floored):
    assert _is_floored_command(name, {"command": cmd}) is floored


def test_console_gate_floors_destructive_under_yolo(tmp_path):
    # console=None + yolo=True: a catastrophic command must be BLOCKED, not True.
    gate = _selective_gate(console=None, yolo=True, root=tmp_path, extra_gated=set())
    for name, cmd in [("run_command", "rm -rf /"), ("run_background", "git reset --hard")]:
        r = gate(name, {"command": cmd})
        assert r is not True, f"{name} {cmd} was auto-approved under yolo"
        assert isinstance(r, str) and "blocked" in r.lower()


def test_console_gate_allows_safe_command_under_yolo(tmp_path):
    gate = _selective_gate(console=None, yolo=True, root=tmp_path, extra_gated=set())
    assert gate("run_command", {"command": "ls -la"}) is True
    assert gate("read_file", {"path": "x"}) is True
