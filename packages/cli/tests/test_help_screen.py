"""Tests for the grouped /help screen."""
from __future__ import annotations

from ronin_cli.code_mode import _HELP_GROUPS, _render_help, SLASH_COMMANDS


def test_every_grouped_command_is_real() -> None:
    # no group references a command that doesn't exist
    for _, names in _HELP_GROUPS:
        for n in names:
            assert n in SLASH_COMMANDS, f"/help references unknown command /{n}"


def test_no_duplicate_placement() -> None:
    placed = [n for _, names in _HELP_GROUPS for n in names]
    assert len(placed) == len(set(placed)), "a command is listed in two groups"


def test_renders_without_crashing() -> None:
    from rich.console import Console
    _render_help(Console(file=open("/dev/null", "w")))


def test_help_groups_nonempty() -> None:
    assert len(_HELP_GROUPS) >= 4
    assert all(names for _, names in _HELP_GROUPS)
