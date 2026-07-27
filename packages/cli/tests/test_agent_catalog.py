"""Tests for scalable, bounded specialist-profile selection."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ronin_cli.agent_catalog import AgentProfile, build_catalog, generated_profiles, select_profiles
from ronin_cli.main import app
from ronin_cli.orchestrate import core_agent_profiles, select_agents_for_goal


def test_generated_catalog_has_more_than_a_thousand_unique_profiles() -> None:
    profiles = generated_profiles()
    assert len(profiles) >= 1_000
    assert len({profile.key for profile in profiles}) == len(profiles)
    assert any(profile.key == "payments-security-auditor" for profile in profiles)
    assert next(profile for profile in profiles if profile.key == "testing-tester").tier == "test"


def test_selection_keeps_core_team_and_adds_relevant_specialists(tmp_path: Path) -> None:
    core = core_agent_profiles()
    catalog = build_catalog(core, tmp_path)

    selected = select_profiles(
        "audit payments security and Python API code", catalog,
        core_keys=(profile.key for profile in core), max_agents=8,
    )

    assert [profile.key for profile in selected[:4]] == [profile.key for profile in core]
    assert "payments-security-auditor" in {profile.key for profile in selected}
    assert len(selected) <= 8


def test_selection_matches_singular_task_terms_to_plural_domain_tags(tmp_path: Path) -> None:
    selected = select_agents_for_goal("audit payment security", tmp_path, max_agents=8)

    assert "payments-security-auditor" in {profile.key for profile in selected}


def test_project_manifest_profiles_are_selected_without_code_execution(tmp_path: Path) -> None:
    manifest = tmp_path / ".ronin" / "agents.json"
    manifest.parent.mkdir()
    manifest.write_text(json.dumps({"agents": [{
        "id": "ledger-reconciler",
        "description": "Reconciles ledger and billing flows",
        "instructions": "Trace ledger invariants and report evidence.",
        "tags": ["ledger", "billing"],
        "tier": "test",
    }]}), encoding="utf-8")

    selected = select_agents_for_goal("debug ledger billing mismatch", tmp_path, max_agents=8)

    profile = next(profile for profile in selected if profile.key == "ledger-reconciler")
    assert profile.tier == "test"
    assert profile.source == "project"


def test_catalog_refuses_profile_collisions_and_invalid_team_sizes(tmp_path: Path) -> None:
    manifest = tmp_path / ".ronin" / "agents.json"
    manifest.parent.mkdir()
    manifest.write_text(json.dumps({"agents": [{
        "id": "researcher",
        "description": "Collides with the core role",
        "instructions": "Never used.",
        "tags": ["research"],
    }]}), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        select_agents_for_goal("research auth", tmp_path)

    with pytest.raises(ValueError, match="at least 4"):
        select_agents_for_goal("research auth", tmp_path, max_agents=3, manifest=tmp_path / "missing.json")


def test_agents_cli_reports_catalog_and_selected_team(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, [
        "agents", "audit payments security", "--root", str(tmp_path), "--max-agents", "6",
    ])

    assert result.exit_code == 0, result.stdout
    assert "available" in result.stdout
    assert "payments-security-auditor" in result.stdout


def test_agent_profile_rejects_unsafe_or_malformed_definitions() -> None:
    with pytest.raises(ValueError, match="invalid agent profile id"):
        AgentProfile("Bad ID", "d", "s", ())
    with pytest.raises(ValueError, match="invalid tier"):
        AgentProfile("valid-id", "d", "s", (), tier="shell")


def test_project_manifest_rejects_oversized_profile_text(tmp_path: Path) -> None:
    manifest = tmp_path / ".ronin" / "agents.json"
    manifest.parent.mkdir()
    manifest.write_text(json.dumps({"agents": [{
        "id": "oversized-profile",
        "description": "Too much instruction text",
        "instructions": "x" * 6_001,
        "tags": ["test"],
    }]}), encoding="utf-8")

    with pytest.raises(ValueError, match="profile text safety limit"):
        select_agents_for_goal("test", tmp_path)
