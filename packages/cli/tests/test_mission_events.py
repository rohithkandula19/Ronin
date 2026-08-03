"""Contracts for the durable schema-first mission event bus."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ronin_cli.main import app
from ronin_cli.mission_events import MissionEventBus, MissionEventPayload
from ronin_cli.mission_models import MissionArtifacts, MissionSpec, MissionStage, PlanArtifact, PlanStep
from ronin_cli.mission_store import MissionStore


def test_event_bus_is_idempotent_and_detects_hash_chain_tampering(tmp_path: Path) -> None:
    bus = MissionEventBus(tmp_path)
    payload = MissionEventPayload(audit_kind="mission_created")
    first = bus.publish(
        topic="mission.created", mission_id="mission-20260803-120000-abcdef", producer="operator",
        causation_id="a" * 64, idempotency_key="issue:42:created", occurred_at="2026-08-03T12:00:00+00:00",
        payload=payload,
    )
    repeated = bus.publish(
        topic="mission.created", mission_id="mission-20260803-120000-abcdef", producer="operator",
        causation_id="a" * 64, idempotency_key="issue:42:created", occurred_at="2026-08-03T12:00:00+00:00",
        payload=payload,
    )

    assert repeated.id == first.id
    assert len(bus.events()) == 1
    assert bus.verify().valid
    path = tmp_path / ".ronin" / "mission-events" / "events.jsonl"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["topic"] = "test.failed"
    path.write_text(json.dumps(raw) + "\n", encoding="utf-8")
    assert not bus.verify().valid
    with pytest.raises(ValueError, match="refusing publish"):
        bus.publish(
            topic="mission.created", mission_id="mission-20260803-120000-abcdef", producer="operator",
            causation_id="b" * 64, idempotency_key="issue:42:second", occurred_at="2026-08-03T12:00:01+00:00",
            payload=payload,
        )


def test_mission_store_publishes_safe_typed_events_and_replay_is_idempotent(tmp_path: Path) -> None:
    store = MissionStore(tmp_path)
    mission = store.create(MissionSpec(
        source="github", source_id="42", title="Keep this title private from the event bus",
        issue_text="This exact issue body must never enter an event envelope.",
    ))
    inspected = store.transition(mission.id, MissionStage.INSPECTING, summary="private issue parsing detail")
    store.save_artifacts(
        inspected.id,
        MissionArtifacts(plan=PlanArtifact(steps=[PlanStep(id="plan", description="private planning detail")])),
    )

    bus = MissionEventBus(tmp_path)
    events = bus.events(mission_id=mission.id)
    assert [event.topic for event in reversed(events)] == [
        "mission.created", "mission.transitioned", "handoff.completed",
    ]
    raw = (tmp_path / ".ronin" / "mission-events" / "events.jsonl").read_text(encoding="utf-8")
    assert "exact issue body" not in raw and "private planning detail" not in raw
    assert bus.verify().valid

    assert store.replay_event_bus(mission.id) == 3
    assert len(bus.events(mission_id=mission.id)) == 3


def test_mission_event_cli_and_dashboard_entries_expose_only_safe_metadata(tmp_path: Path) -> None:
    store = MissionStore(tmp_path)
    mission = store.create(MissionSpec(title="Event UI", issue_text="Do not expose this issue body."))
    store.transition(mission.id, MissionStage.INSPECTING)
    runner = CliRunner()

    verified = runner.invoke(app, ["util", "mission", "events", "verify", "--root", str(tmp_path)])
    listed = runner.invoke(app, ["util", "mission", "events", "list", "--root", str(tmp_path), "--mission", mission.id])
    from csk_api.dashboard import mission_event_entries

    assert verified.exit_code == 0, verified.stdout
    assert listed.exit_code == 0 and "Seq" in listed.stdout and mission.id in listed.stdout
    entries = mission_event_entries(tmp_path)
    assert entries[0]["mission_id"] == mission.id
    assert set(entries[0]) == {
        "id", "sequence", "topic", "mission_id", "producer", "from_stage", "to_stage", "occurred_at",
    }
