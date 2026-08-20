"""Contracts for durable local persistent-agent team supervision."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ronin_cli.main import app
from ronin_cli.persistent_agents import HandoffEvidenceInput, PersistentAgentStore, TEAM_SCHEMA_VERSION


def test_persistent_agent_lifecycle_recovers_a_stale_assignment_and_keeps_audit(tmp_path: Path) -> None:
    store = PersistentAgentStore(tmp_path)
    agents = store.bootstrap(("architect", "reviewer"))
    assert [agent.agent_id for agent in agents] == ["architect-01", "reviewer-01"]

    assigned = store.assign("architect-01", "mission-42", "Design a bounded retry strategy")
    assert assigned.status == "assigned" and assigned.current_mission_id == "mission-42"
    running = store.start("architect-01")
    assert running.status == "running"
    store.heartbeat("architect-01", at="2026-01-01T00:00:00+00:00")

    recovered = store.supervise(now="2026-01-01T00:01:00+00:00")
    assert len(recovered) == 1
    assert recovered[0].status == "assigned"
    assert recovered[0].restart_count == 1
    assert recovered[0].current_task == "Design a bounded retry strategy"

    store.start("architect-01")
    assert store.complete("architect-01", "Proposed retry boundary and tests.").status == "completed"
    assert store.release("architect-01").status == "idle"

    reopened = PersistentAgentStore(tmp_path)
    assert reopened.get("architect-01").status == "idle"
    report = reopened.verify_audit()
    assert report.valid and report.events >= 8
    with sqlite3.connect(reopened.path) as con:
        con.execute("UPDATE agent_events SET kind = 'corrupted' WHERE sequence = 1")
    assert not reopened.verify_audit().valid


def test_persistent_team_schema_v1_upgrades_additively_for_handoffs(tmp_path: Path) -> None:
    store = PersistentAgentStore(tmp_path)
    store.bootstrap(("architect",))
    with sqlite3.connect(store.path) as con:
        con.execute("DROP TABLE agent_handoffs")
        con.execute("PRAGMA user_version = 1")

    upgraded = PersistentAgentStore(tmp_path)
    assert upgraded.list_handoffs() == ()
    with sqlite3.connect(upgraded.path) as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == TEAM_SCHEMA_VERSION
        assert con.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'agent_handoffs'",
        ).fetchone()


def test_experience_memory_is_provenanced_compacted_and_refuses_likely_secrets(tmp_path: Path) -> None:
    store = PersistentAgentStore(tmp_path)
    store.bootstrap(("reviewer",))
    durable = store.remember(
        "reviewer-01", "FastAPI route changes require matching OpenAPI coverage.",
        source_type="pull_request", source_id="#123", confidence=0.9,
    )
    expired = store.remember(
        "reviewer-01", "Temporary retry workaround was flaky.",
        source_type="workaround", source_id="incident-7", confidence=0.2,
        expires_at="2025-01-01T00:00:00+00:00",
    )
    recalled = store.recall("reviewer-01", "FastAPI OpenAPI route")
    assert recalled[0].memory_id == durable.memory_id
    assert recalled[0].source_type == "pull_request" and recalled[0].access_count == 1

    compacted = store.compact_memories("reviewer-01", at="2026-01-01T00:00:00+00:00")
    assert compacted.archived == 1 and compacted.summary_memory_id
    assert store.recall("reviewer-01", "retry workaround", at="2026-01-01T00:00:00+00:00")[0].source_type == "compaction"
    assert expired.memory_id != compacted.summary_memory_id

    with pytest.raises(ValueError, match="possible secret"):
        store.remember(
            "reviewer-01", "AWS key " + "AKIA" + "ZX7Q2RSTUV3BWXYC",
            source_type="human", source_id="operator", confidence=1.0,
        )


def test_context_pack_combines_repo_conventions_tests_and_role_experience_with_budget(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "RONIN.md").write_text("## Conventions\n- Use structured logging.\n", encoding="utf-8")
    (tmp_path / "src" / "retry.py").write_text(
        "def retry_request(client):\n    return client.send()\n", encoding="utf-8",
    )
    (tmp_path / "tests" / "test_retry.py").write_text(
        "def test_retry_request():\n    assert True\n", encoding="utf-8",
    )
    store = PersistentAgentStore(tmp_path)
    store.bootstrap(("tester",))
    store.remember(
        "tester-01", "Retry changes need a bounded failure test.",
        source_type="mission", source_id="mission-7", confidence=0.85,
    )

    pack = store.context_pack("tester-01", "add retry coverage", mission_id="mission-7", max_tokens=128)
    assert pack.mission_id == "mission-7" and pack.estimated_tokens <= pack.max_tokens
    assert {item.kind for item in pack.items} >= {"convention", "file", "test", "experience"}
    assert any(item.reference == "src/retry.py" for item in pack.items)
    assert any(item.reference == "tests/test_retry.py" for item in pack.items)


def test_persistent_handoff_requires_completed_source_acknowledgement_and_safe_metadata(tmp_path: Path) -> None:
    store = PersistentAgentStore(tmp_path)
    store.bootstrap(("architect", "implementer"))
    store.assign("architect-01", "mission-42", "Design the retry boundary")
    store.assign("implementer-01", "mission-42", "Implement the bounded retry policy")
    store.start("architect-01")
    store.complete("architect-01", "Recorded a bounded implementation plan.")

    handoff = store.create_handoff(
        "architect-01", "implementer-01", "mission-42", "Plan is ready for implementation.",
        (HandoffEvidenceInput(kind="plan", reference="mission-42:retry-plan-v1"),),
    )
    assert handoff.status == "pending" and handoff.evidence[0].digest
    assert store.get("implementer-01").status == "assigned"
    assert PersistentAgentStore(tmp_path).get_handoff(handoff.handoff_id).summary == handoff.summary

    acknowledged = store.acknowledge_handoff(handoff.handoff_id, "implementer-01")
    assert acknowledged.status == "accepted" and acknowledged.accepted_at
    assert store.get("implementer-01").status == "assigned"
    assert [event.kind for event in store.audit_events(limit=20)][0] == "handoff_acknowledged"
    assert store.verify_audit().valid

    with pytest.raises(ValueError, match="already been acknowledged"):
        store.acknowledge_handoff(handoff.handoff_id, "implementer-01")
    with pytest.raises(ValueError, match="possible secret"):
        store.create_handoff(
            "architect-01", "implementer-01", "mission-42", "AKIA" + "ZX7Q2RSTUV3BWXYC",
            (HandoffEvidenceInput(kind="plan", reference="mission-42:retry-plan-v2"),),
        )


def test_team_cli_and_dashboard_surface_status_only_metadata(tmp_path: Path) -> None:
    runner = CliRunner()
    initialized = runner.invoke(app, ["util", "team", "init", "--root", str(tmp_path)])
    assigned = runner.invoke(
        app,
        ["util", "team", "assign", "architect-01", "mission-42", "Design the retry boundary", "--root", str(tmp_path)],
    )
    status = runner.invoke(app, ["util", "team", "status", "--root", str(tmp_path)])
    started = runner.invoke(app, ["util", "team", "start", "architect-01", "--root", str(tmp_path)])
    completed = runner.invoke(app, ["util", "team", "complete", "architect-01", "--root", str(tmp_path)])
    assigned_implementer = runner.invoke(
        app,
        ["util", "team", "assign", "implementer-01", "mission-42", "Implement the retry boundary", "--root", str(tmp_path)],
    )
    handoff = runner.invoke(
        app,
        [
            "util", "team", "handoff", "architect-01", "implementer-01", "mission-42",
            "--summary", "Design is ready for implementation.", "--evidence", "plan:mission-42:retry-plan-v1",
            "--root", str(tmp_path),
        ],
    )
    handoff_id = next(token for token in handoff.stdout.split() if token.startswith("handoff-"))
    accepted = runner.invoke(
        app, ["util", "team", "accept-handoff", handoff_id, "implementer-01", "--root", str(tmp_path)],
    )
    handoffs = runner.invoke(app, ["util", "team", "handoffs", "--root", str(tmp_path)])
    audit = runner.invoke(app, ["util", "team", "audit", "--root", str(tmp_path)])
    from csk_api.dashboard import persistent_agent_entries, persistent_handoff_entries

    assert initialized.exit_code == 0, initialized.stdout
    assert assigned.exit_code == 0, assigned.stdout
    assert started.exit_code == 0, started.stdout
    assert completed.exit_code == 0, completed.stdout
    assert assigned_implementer.exit_code == 0, assigned_implementer.stdout
    assert handoff.exit_code == 0, handoff.stdout
    assert accepted.exit_code == 0, accepted.stdout
    assert handoffs.exit_code == 0 and handoff_id in handoffs.stdout
    assert PersistentAgentStore(tmp_path).get_handoff(handoff_id).status == "accepted"
    assert status.exit_code == 0 and "architect-01" in status.stdout and "assigned" in status.stdout
    assert audit.exit_code == 0 and "valid" in audit.stdout
    entry = next(item for item in persistent_agent_entries(tmp_path) if item["agent_id"] == "architect-01")
    assert entry["mission_id"] == "mission-42"
    assert set(entry) == {
        "agent_id", "role", "status", "mission_id", "restart_count", "health_check_at", "updated_at",
    }
    assert "Design the retry boundary" not in str(entry)
    handoff_entry = persistent_handoff_entries(tmp_path)[0]
    assert handoff_entry["status"] == "accepted" and handoff_entry["evidence_count"] == 1
    assert set(handoff_entry) == {
        "handoff_id", "mission_id", "from_agent_id", "to_agent_id", "status", "evidence_count",
        "created_at", "accepted_at", "updated_at",
    }
    assert "Design is ready for implementation" not in str(handoff_entry)
