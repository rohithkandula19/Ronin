from __future__ import annotations

import json
from pathlib import Path

import pytest

from ronin_cli.agent_catalog import AgentProfile
from ronin_cli.agent_queue import AgentQueue, QueueExecution
from ronin_cli.agent_routing import ModelCandidate, route_profiles
from ronin_cli.autonomy_ledger import AutonomyLedger, verify_ledger
from ronin_cli.code_tools import build_code_tools
from ronin_cli.constitution import RepositoryConstitution, load_constitution
from ronin_cli.orchestrate import core_agent_profiles, run_orchestrate
from ronin_cli.project_memory import ProjectMemory, build_project_memory_tools
from ronin_cli.config import RoninConfig
from ronin_cli.worktree_trials import TrialResult, choose_trial


def test_constitution_blocks_writes_but_not_required_read_access(tmp_path: Path) -> None:
    protected = tmp_path / "migrations" / "001.sql"
    protected.parent.mkdir()
    protected.write_text("select 1;", encoding="utf-8")
    policy = RepositoryConstitution(protected_paths=("migrations/**",))
    tools = {
        tool.name: tool.handler
        for tool in build_code_tools(tmp_path, write_deny=lambda path: policy.protects(path, root=tmp_path))
    }

    assert tools["read_file"]("migrations/001.sql") == "select 1;"
    assert "protected by the repository constitution" in tools["edit_file"](
        "migrations/001.sql", "select 1;", "select 2;"
    )
    assert protected.read_text(encoding="utf-8") == "select 1;"


def test_constitution_requires_matching_specialist_suffix_and_orchestrator_enforces_it(tmp_path: Path) -> None:
    policy_path = tmp_path / ".ronin" / "constitution.json"
    policy_path.parent.mkdir()
    policy_path.write_text(json.dumps({"required_roles": {"security": ["security-auditor"]}}), encoding="utf-8")
    policy = load_constitution(tmp_path)
    policy.validate_team(["payments-security-auditor"], tags=["security"], write=False)
    with pytest.raises(ValueError, match="security-auditor"):
        policy.validate_team(["researcher"], tags=["security"], write=False)

    outcome = run_orchestrate(
        RoninConfig(provider="local", offline=True), "security review", root=tmp_path,
        profiles=(core_agent_profiles()[0],), read_only=True,
    )
    assert outcome.success is False
    assert "constitution requires role" in (outcome.error or "")


def test_local_ledger_is_hash_chained_and_detects_tampering(tmp_path: Path) -> None:
    ledger = AutonomyLedger(tmp_path)
    ledger.append("run", run_id="agent-1", goal="private goal", payload={"success": True})
    ledger.append("run", run_id="agent-2", goal="another", payload={"success": False})
    assert verify_ledger(tmp_path).valid

    path = tmp_path / ".ronin" / "autonomy-ledger.jsonl"
    path.write_text(path.read_text(encoding="utf-8").replace('"success":true', '"success":false', 1), encoding="utf-8")
    report = verify_ledger(tmp_path)
    assert report.valid is False
    assert "hash" in (report.error or "")


def test_project_memory_is_durable_local_and_rejects_secrets(tmp_path: Path) -> None:
    store = ProjectMemory(tmp_path)
    record = store.remember("Use pytest -q before merging", tags=["test"])
    assert record
    assert ProjectMemory(tmp_path).recall("how should we test", k=1)[0]["text"].startswith("Use pytest")
    with pytest.raises(ValueError, match="possible secret"):
        store.remember("api_key=super-secret-value")
    tools = {tool.name: tool for tool in build_project_memory_tools(tmp_path)}
    assert tools["project_memory_remember"].sensitive is True


def test_evidence_router_prefers_quality_for_writes_and_cost_for_exploration() -> None:
    write = AgentProfile("writer", "writes", "write", ("code",), tier="write")
    explore = AgentProfile("explorer", "explores", "explore", ("code",), tier="explore")
    strong = ModelCandidate("strong", quality=.95, cost=.9, latency=.5)
    cheap = ModelCandidate("cheap", quality=.45, cost=.05, latency=.3)
    decisions = {row.role: row for row in route_profiles([write, explore], [strong, cheap])}
    assert decisions["writer"].spec == "strong"
    assert decisions["explorer"].spec == "cheap"


def test_trial_selection_and_parallel_queue_require_terminal_evidence(tmp_path: Path) -> None:
    decision = choose_trial([
        TrialResult("broken", "", True, False, quality=1.0),
        TrialResult("verified", "", True, True, quality=.5),
    ])
    assert decision.winner is not None and decision.winner.name == "verified"

    queue = AgentQueue(tmp_path)
    queue.enqueue("first")
    queue.enqueue("second")
    with pytest.raises(ValueError, match="max_parallel"):
        queue.run_pending(lambda _job: QueueExecution(True), max_jobs=2, max_parallel=101)
    assert all(job.status == "queued" for job in queue.list())
    seen: list[str] = []

    def worker(job):
        seen.append(job.id)
        return QueueExecution(True, run_id=f"run-{job.id}")

    finished = queue.run_pending(worker, max_jobs=2, max_parallel=2)
    assert len(finished) == 2
    assert all(job.status == "completed" for job in finished)
    assert len(set(seen)) == 2
