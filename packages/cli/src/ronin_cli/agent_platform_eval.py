"""Offline regression checks for the integrated agent-platform surfaces."""
from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from .agent_observability import provider_observations
from .agent_queue import AgentQueue
from .agent_state import AgentTaskStateStore
from .autonomy_ledger import AutonomyLedger, verify_ledger
from .constitution import RepositoryConstitution
from .config import RoninConfig
from .embeddings import semantic_search
from .project_memory import ProjectMemory
from .sandbox_policy import inspect_sandbox_policy


@dataclass(frozen=True)
class PlatformEvalOutcome:
    case: str
    passed: bool
    detail: str


def run_agent_platform_eval() -> list[PlatformEvalOutcome]:
    """Exercise durable queue, local retrieval, health, and sandbox policy offline."""
    with tempfile.TemporaryDirectory(prefix="ronin-platform-eval-") as temp:
        root = Path(temp)
        outcomes = [
            _queue_case(root),
            _telemetry_case(root),
            _semantic_case(root),
            _sandbox_case(),
            _constitution_case(root),
            _ledger_case(root),
            _memory_case(root),
        ]
    return outcomes


def render_agent_platform_report(console, outcomes: list[PlatformEvalOutcome]) -> bool:
    from rich.table import Table

    table = Table(show_header=True, header_style="bold")
    table.add_column("case")
    table.add_column("result")
    table.add_column("detail")
    for outcome in outcomes:
        table.add_row(
            outcome.case,
            "[green]pass[/green]" if outcome.passed else "[red]fail[/red]",
            outcome.detail,
        )
    console.print(table)
    passed = sum(outcome.passed for outcome in outcomes)
    console.print(f"agent platform eval: {passed}/{len(outcomes)} passed")
    return passed == len(outcomes)


def _queue_case(root: Path) -> PlatformEvalOutcome:
    queue = AgentQueue(root)
    queued = queue.enqueue("add retry coverage", write=True)
    claimed = queue.claim_next()
    complete = queue.finish(queued.id, success=True, run_id="agent-demo")
    passed = claimed is not None and claimed.status == "running" and complete is not None and complete.status == "completed"
    return PlatformEvalOutcome("queue lifecycle", passed, "queued -> running -> completed")


def _telemetry_case(root: Path) -> PlatformEvalOutcome:
    store = AgentTaskStateStore(root)
    state = store.create("inspect provider", selection={"profiles": ["researcher"]}, workflow={}, governance={})
    store.finish(
        state, success=True, output="done", error=None,
        provider_health={"local:model": {"attempts": 1, "failures": 0, "roles": ["researcher"]}},
    )
    health = provider_observations(root).get("local:model", {})
    passed = health.get("status") == "healthy" and health.get("attempts") == 1
    return PlatformEvalOutcome("provider telemetry", passed, "observed success is retained locally")


def _semantic_case(root: Path) -> PlatformEvalOutcome:
    (root / "auth.py").write_text("def verify_credentials(token):\n    return bool(token)\n", encoding="utf-8")
    (root / "report.py").write_text("def render_chart(values):\n    return values\n", encoding="utf-8")
    results = semantic_search(
        "authenticate a user token", root, RoninConfig(provider="local", offline=True), k=1,
    )
    passed = bool(results) and results[0][1] == "auth.py"
    return PlatformEvalOutcome("local semantic retrieval", passed, "offline hashing ranks the authentication file")


def _sandbox_case() -> PlatformEvalOutcome:
    status = inspect_sandbox_policy("docker:agent", executable=lambda _name: None)
    passed = status.status == "blocked" and status.fail_closed
    return PlatformEvalOutcome("sandbox fail-closed", passed, "unavailable requested sandbox blocks host fallback")


def _constitution_case(root: Path) -> PlatformEvalOutcome:
    policy = RepositoryConstitution(protected_paths=("migrations/**",))
    target = root / "migrations" / "001.sql"
    target.parent.mkdir(exist_ok=True)
    target.write_text("select 1", encoding="utf-8")
    return PlatformEvalOutcome("repository constitution", policy.protects(target, root=root), "protected writes are policy-addressable")


def _ledger_case(root: Path) -> PlatformEvalOutcome:
    AutonomyLedger(root).append("eval", run_id="eval-1", goal="private", payload={"success": True})
    report = verify_ledger(root)
    return PlatformEvalOutcome("autonomy ledger", report.valid and report.events == 1, "local hash chain verifies")


def _memory_case(root: Path) -> PlatformEvalOutcome:
    ProjectMemory(root).remember("Use the local test suite before merging", tags=["test"])
    rows = ProjectMemory(root).recall("how should we test", k=1)
    return PlatformEvalOutcome("project memory", bool(rows), "SQLite hashing retrieval remains local")
