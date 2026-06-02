"""Tests for Muscle Memory — crystallizing solved workflows into /skills."""
from __future__ import annotations

from pathlib import Path

import pytest

from ronin_cli.muscle_memory import (
    build_muscle_tool,
    crystallize,
    list_skills,
    normalize_name,
)


def test_normalize_name() -> None:
    assert normalize_name("Add Endpoint") == "add-endpoint"
    assert normalize_name("  Cut a Release!! ") == "cut-a-release"


def test_crystallize_writes_runnable_skill(tmp_path: Path) -> None:
    path, slug = crystallize(tmp_path, "Add Endpoint", "Add a REST endpoint for $ARGUMENTS")
    assert slug == "add-endpoint"
    assert path == tmp_path / ".csk" / "commands" / "add-endpoint.md"
    assert "Add a REST endpoint for $ARGUMENTS" in path.read_text(encoding="utf-8")
    assert list_skills(tmp_path) == ["add-endpoint"]


def test_crystallize_appends_arguments_if_missing(tmp_path: Path) -> None:
    path, _ = crystallize(tmp_path, "deploy", "Run the deploy checklist")
    assert "$ARGUMENTS" in path.read_text(encoding="utf-8")


def test_crystallize_rejects_builtin_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        crystallize(tmp_path, "help", "shadow the builtin")


def test_crystallize_rejects_empty_body(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        crystallize(tmp_path, "thing", "   ")


def test_skill_is_runnable_as_custom_command(tmp_path: Path) -> None:
    # crystallized skill must be picked up by the custom-command runner as /name
    from ronin_cli.code_mode import expand_custom_command
    crystallize(tmp_path, "greet", "Say hello to $ARGUMENTS warmly")
    out = expand_custom_command("/greet the team", tmp_path)
    assert out == "Say hello to the team warmly"


def test_tool_handler(tmp_path: Path) -> None:
    tool = build_muscle_tool(tmp_path)[0]
    assert tool.name == "crystallize_skill"
    out = tool.handler(name="add-test", template="Write a pytest for $ARGUMENTS")
    assert "/add-test" in out
    assert "add-test" in list_skills(tmp_path)
    # bad name surfaces an error string, not an exception
    assert tool.handler(name="help", template="x").startswith("ERROR")
