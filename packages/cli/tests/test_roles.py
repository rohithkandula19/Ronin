"""Tests for the Wave 3 role system (pure registry / state / suggestion)."""
from __future__ import annotations

import pytest

from ronin_cli import roles


@pytest.fixture(autouse=True)
def _reset_role():
    roles.set_role(None)
    yield
    roles.set_role(None)


def test_six_builtin_roles_exist() -> None:
    assert set(roles.ROLES) == {
        "researcher", "implementer", "reviewer", "tester", "architect", "debugger",
        "verifier",
    }


def test_read_only_roles_are_research_review_architect() -> None:
    ro = {k for k, r in roles.ROLES.items() if r.read_only}
    assert ro == {"researcher", "reviewer", "architect", "verifier"}
    # the doers are not read-only
    for k in ("implementer", "tester", "debugger"):
        assert roles.role_is_read_only(k) is False


def test_parse_role_normalizes_and_validates() -> None:
    assert roles.parse_role("Reviewer") == "reviewer"
    assert roles.parse_role(" debugger ") == "debugger"
    assert roles.parse_role("clear") == "clear"
    assert roles.parse_role("off") == "clear"
    assert roles.parse_role("wizard") is None  # unknown
    assert roles.parse_role(None) is None


def test_set_get_clear_role_persists() -> None:
    assert roles.current_role() is None
    assert roles.set_role("debugger") == "debugger"
    assert roles.current_role() == "debugger"          # persists across calls
    assert roles.set_role("nonsense") == "debugger"    # unknown ignored
    assert roles.set_role("clear") is None
    assert roles.current_role() is None


def test_role_guidance_present_and_read_only_safe() -> None:
    g = roles.role_guidance("researcher")
    assert "READ-ONLY" in g and "Do NOT" in g
    # implementer guidance must reinforce that edits stay gated
    assert "approval" in roles.role_guidance("implementer").lower()
    assert roles.role_guidance("unknown") == ""


def test_role_help_lines_cover_all_roles() -> None:
    lines = roles.role_help_lines()
    assert len(lines) == len(roles.ROLES)
    joined = "\n".join(lines)
    for k in roles.ROLES:
        assert f"/role {k}" in joined


@pytest.mark.parametrize(
    "task, expected",
    [
        ("why is this test failing?", "debugger"),
        ("can you review my PR for bugs?", "reviewer"),
        ("add a feature to export CSV", "implementer"),
        ("add tests for the parser", "tester"),
        ("how should I structure the auth module?", "architect"),
        ("how does the router pick a model?", "researcher"),
        ("hello there", None),
    ],
)
def test_suggest_role(task, expected) -> None:
    assert roles.suggest_role(task) == expected


# --- role suggestion line (surfaced, never forced) ---------------------------

def test_suggestion_line_only_when_no_role_active() -> None:
    line = roles.role_suggestion_line("why is this failing?", current=None)
    assert line is not None and "/role debugger" in line
    # with a role already active, no suggestion (don't nag)
    assert roles.role_suggestion_line("why is this failing?", current="implementer") is None


def test_suggestion_line_none_for_neutral_task() -> None:
    assert roles.role_suggestion_line("hello there", current=None) is None


def test_suggestion_line_points_to_command_not_autoswitch() -> None:
    line = roles.role_suggestion_line("review my PR", current=None)
    assert "/role reviewer" in line
    # purely a tip — calling it must not change the active role
    assert roles.current_role() is None
