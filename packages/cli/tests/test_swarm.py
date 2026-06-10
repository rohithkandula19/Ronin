"""Tests for the agent swarm — roster parsing + role resolution."""
from __future__ import annotations

from ronin_cli.config import RoninConfig
from ronin_cli.swarm import RoleAssignment, parse_roster, role_label


def test_parse_full_roster() -> None:
    ra = parse_roster("architect=anthropic,implementer=cerebras:gpt-oss-120b,reviewer=gemini")
    assert ra.architect == "anthropic"
    assert ra.implementer == "cerebras:gpt-oss-120b"
    assert ra.reviewer == "gemini"


def test_parse_partial_roster() -> None:
    ra = parse_roster("implementer=groq")
    assert ra.implementer == "groq"
    assert ra.architect is None and ra.reviewer is None


def test_parse_ignores_unknown_roles_and_junk() -> None:
    ra = parse_roster("architect=anthropic,bogus=x,malformed")
    assert ra.architect == "anthropic" and ra.implementer is None


def test_parse_empty() -> None:
    assert parse_roster(None) == RoleAssignment()
    assert parse_roster("") == RoleAssignment()


def test_for_role() -> None:
    ra = parse_roster("reviewer=gemini")
    assert ra.for_role("reviewer") == "gemini"
    assert ra.for_role("architect") is None


def test_role_label_uses_override_or_base() -> None:
    base = RoninConfig(provider="anthropic")
    ra = parse_roster("implementer=cerebras:gpt-oss-120b")
    # implementer overridden → cerebras; architect falls back to base → anthropic
    assert "cerebras" in role_label(base, ra, "implementer")
    assert "anthropic" in role_label(base, ra, "architect")
