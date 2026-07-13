"""The destructive floor must survive shell-equivalent reformatting, not just
the exact ``rm -rf`` spelling (vNext Phase 0 adversarial finding P1). Before this,
``is_destructive_command`` was a naive substring match: ``rm  -rf  ~`` (extra
spaces), ``rm -r -f ~`` (split flags), ``rm --recursive --force /``,
``find / -delete`` and ``chmod -R 000 /`` all slipped through and auto-approved
under ``--yolo`` — while the shell runs them identically to the blocked form."""
from __future__ import annotations

import pytest

from ronin_cli.approvals import is_destructive_command, is_floored_tool_call


# Every one of these is catastrophic and the shell normalizes it to (or runs it
# identically to) a form the floor already blocks. All MUST now be caught.
CATASTROPHIC_NOW_BLOCKED = [
    "rm  -rf  ~",                 # collapsed whitespace
    "rm -rf  /",
    "rm -r -f ~",                 # split short flags
    "rm -f -r /",                 # reversed order
    "rm --recursive --force /",   # long options
    "rm --force --recursive ~",
    "rm -rf ./ && echo done",     # in a compound command
    "find / -delete",
    "find . -delete",
    "find / -exec rm -rf {} ;",
    "chmod -R 000 /",
    "chmod -r 777 ~",             # recursive chmod of home
    "chown -R nobody /",
    "cat /dev/zero > /dev/sda",
    "echo x > /dev/nvme0n1",
    "dd if=/dev/zero of=/dev/sdb",
    "RM  -RF  /",                 # case-insensitive
]

# Ordinary, safe commands that must STAY allowed — the floor must not become a
# false-positive nuisance.
SAFE_STAYS_ALLOWED = [
    "rm file.txt",
    "rm -f onefile",              # single-file force, not recursive
    "rm -i thing",
    "ls -la",
    "git status",
    "git checkout main",
    "git clean -n",               # dry run
    "chmod -R 755 ./build",       # recursive but not root/home
    "chmod 644 file",
    "chown -R me ./dir",
    "find . -name '*.py'",
    "find . -type f -print",
    "cat file.txt > out.txt",
    "echo hi > notes.md",
    "grep -rf patterns.txt .",    # -rf here is grep flags, no rm/recursive-delete
]

# Canonical catastrophic forms that were already blocked must STAY blocked.
CANONICAL_STILL_BLOCKED = [
    "rm -rf /",
    "rm -rf ~",
    "git reset --hard",
    "git clean -fdx",
    "dd if=/dev/zero of=/dev/sda",
    "mkfs.ext4 /dev/sda",
    ":(){ :|:& };:",
    "drop table users",
]


@pytest.mark.parametrize("cmd", CATASTROPHIC_NOW_BLOCKED)
def test_reformatted_catastrophic_is_blocked(cmd):
    assert is_destructive_command(cmd) is True, f"floor missed: {cmd!r}"


@pytest.mark.parametrize("cmd", SAFE_STAYS_ALLOWED)
def test_safe_command_stays_allowed(cmd):
    assert is_destructive_command(cmd) is False, f"false positive: {cmd!r}"


@pytest.mark.parametrize("cmd", CANONICAL_STILL_BLOCKED)
def test_canonical_catastrophic_still_blocked(cmd):
    assert is_destructive_command(cmd) is True, f"regressed: {cmd!r}"


def test_is_floored_tool_call_is_the_shared_floor():
    # Command tools with a destructive command are floored, including the
    # reformatted spellings.
    assert is_floored_tool_call("run_command", {"command": "rm  -rf  ~"}) is True
    assert is_floored_tool_call("run_background", {"command": "find / -delete"}) is True
    # Safe command → not floored.
    assert is_floored_tool_call("run_command", {"command": "ls -la"}) is False
    # Non-command tool is never floored (its args aren't a shell command).
    assert is_floored_tool_call("write_file", {"command": "rm -rf /"}) is False
    # Fail-closed on malformed args → not a command → not destructive (empty).
    assert is_floored_tool_call("run_command", {}) is False
    assert is_floored_tool_call("run_command", None) is False
