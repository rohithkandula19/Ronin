"""The destructive floor must block irrecoverable git data-loss commands even
under --yolo / god-mode (Phase 1 P1 finding: git reset --hard / git clean -f
were auto-approving). Order-robust, and must NOT flag safe git usage."""
from __future__ import annotations

import pytest

from ronin_cli.approvals import is_destructive_command


@pytest.mark.parametrize("cmd", [
    "git reset --hard",
    "git reset --hard HEAD~3",
    "git reset --hard origin/main",
    "cd /repo && git reset --hard",
])
def test_git_reset_hard_is_blocked(cmd):
    assert is_destructive_command(cmd), cmd


@pytest.mark.parametrize("cmd", [
    "git clean -fdx",
    "git clean -xfd",     # flags reordered
    "git clean -df",
    "git clean -f",
    "git clean --force",
    "git clean -fd -x",
])
def test_forced_git_clean_is_blocked_any_flag_order(cmd):
    assert is_destructive_command(cmd), cmd


@pytest.mark.parametrize("cmd", [
    "git checkout main",          # branch switch — must NOT block
    "git switch feature",
    "git status",
    "git reset --soft HEAD~1",    # soft reset keeps changes
    "git clean -n",               # dry run — no force, safe
    "git clean --dry-run",
    "git add -A",
    "git commit -m 'x'",
    "git pull",
])
def test_safe_git_is_not_blocked(cmd):
    assert not is_destructive_command(cmd), cmd


def test_existing_markers_still_block():
    assert is_destructive_command("rm -rf /")
    assert is_destructive_command("git push --force")
    assert is_destructive_command("dd if=/dev/zero of=/dev/sda")
