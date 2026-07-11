"""`ronin investigate` runs its own approval gate, and before the vNext Phase 0
fix it had NO destructive floor: `investigate_mode._gate(console, yolo=True)`
auto-approved every `run_command` — even `rm -rf /` — making `ronin investigate
--yolo` a full floor bypass. The gate now shares the same floor as code mode:
a catastrophic command never auto-approves under yolo (headless denies)."""
from __future__ import annotations

import pytest

from ronin_cli import investigate_mode


CATASTROPHIC = [
    "rm -rf /",
    "rm  -rf  ~",          # reformatted variants are floored too
    "find / -delete",
    "dd if=/dev/zero of=/dev/sda",
    "git reset --hard",
    "git clean -fdx",
]


@pytest.mark.parametrize("cmd", CATASTROPHIC)
def test_investigate_floors_catastrophic_under_yolo(cmd):
    # console=None (headless) + yolo=True: a catastrophic command must NOT be
    # auto-approved. It drops to the human path, which denies when headless.
    gate = investigate_mode._gate(None, True)
    result = gate("run_command", {"command": cmd})
    assert result is not True, f"investigate auto-approved {cmd!r} under yolo"
    assert result is False


def test_investigate_allows_safe_command_under_yolo():
    gate = investigate_mode._gate(None, True)
    assert gate("run_command", {"command": "ls -la"}) is True
    assert gate("run_command", {"command": "grep -r foo ."}) is True


def test_investigate_nonsensitive_tool_passes():
    gate = investigate_mode._gate(None, True)
    assert gate("read_file", {"path": "x"}) is True
