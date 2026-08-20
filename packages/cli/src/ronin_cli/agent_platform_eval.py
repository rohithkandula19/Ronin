"""Offline regression checks for the integrated agent-platform surfaces."""
from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .agent_observability import provider_observations
from .agent_proposals import AgentProposalStore
from .agent_catalog import AgentProfile, RepositorySignals
from .agent_fleet import FleetPlanStore, plan_fleet
from .agent_recovery import load_recovery_context
from .agent_routing import ModelCandidate, route_profiles
from .agent_queue import AgentQueue
from .agent_state import AgentTaskStateStore
from .autonomy_ledger import AutonomyLedger, verify_ledger
from .constitution import RepositoryConstitution
from .config import RoninConfig
from .embeddings import semantic_search
from .project_memory import ProjectMemory
from .provider_health_store import ProviderHealthStore
from .model_scorecards import ModelScorecardStore, apply_scorecards
from .patch_verify import verify_patch
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
            _recovery_case(root),
            _provider_health_case(root),
            _scorecard_case(root),
            _structured_patch_case(root),
            _proposal_case(root),
            _fleet_case(root),
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


def _recovery_case(root: Path) -> PlatformEvalOutcome:
    store = AgentTaskStateStore(root)
    state = store.create(
        "repair retry", selection={"profiles": ["researcher"]}, workflow={}, governance={},
        execution={"read_only": True},
    )
    state["status"] = "failed"
    state["plan"] = [{"id": "inspect"}, {"id": "retry"}]
    state["subtasks"] = {"inspect": {"status": "completed", "success": True}}
    store.save(state)
    context = load_recovery_context(root, state["id"])
    passed = context.completed == ("inspect",) and context.unfinished == ("retry",)
    return PlatformEvalOutcome("agent recovery", passed, "replacement planner receives a status-only handoff")


def _provider_health_case(root: Path) -> PlatformEvalOutcome:
    health = ProviderHealthStore(root)
    health.observe({"local:model": {"attempts": 1, "failures": 1, "roles": ["tester"]}}, now=10)
    cooling = health.view(now=11).get("local:model", {})
    health.observe({"local:model": {"attempts": 1, "failures": 0, "roles": ["tester"]}}, now=50)
    recovered = health.view(now=51).get("local:model", {})
    passed = cooling.get("status") == "cooling-down" and recovered.get("status") == "healthy"
    return PlatformEvalOutcome("provider recovery", passed, "observed success clears a temporary failure cooldown")


def _scorecard_case(root: Path) -> PlatformEvalOutcome:
    store = ModelScorecardStore(root)
    report = root / "eval.json"
    report.write_text('{"model":"cheap","summary":{"resolved_rate":0.95,"total":4}}', encoding="utf-8")
    store.import_report(report)
    candidates = apply_scorecards(
        [ModelCandidate("cheap", quality=.5, cost=.05), ModelCandidate("strong", quality=.85, cost=.8)],
        store.list(),
    )
    profile = AgentProfile("writer", "writes", "write", ("code",), tier="write")
    passed = route_profiles([profile], candidates)[0].spec == "cheap"
    return PlatformEvalOutcome("evaluation scorecard", passed, "stored eval quality guides explicit routing")


def _structured_patch_case(root: Path) -> PlatformEvalOutcome:
    json_result = verify_patch(root / "package.json", "", "{broken")
    toml_result = verify_patch(root / "pyproject.toml", "", "[project\nname='x'")
    passed = json_result.checked and not json_result.valid and toml_result.checked and not toml_result.valid
    return PlatformEvalOutcome("structured patch checks", passed, "invalid JSON and TOML are rejected before writes")


def _proposal_case(root: Path) -> PlatformEvalOutcome:
    repo = root / "proposal-repo"
    repo.mkdir()
    try:
        for args in (
            ("init",), ("config", "user.email", "eval@example.test"),
            ("config", "user.name", "Ronin Eval"),
        ):
            subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True)
        (repo / ".gitignore").write_text(".ronin/\n", encoding="utf-8")
        target = repo / "sample.txt"
        target.write_text("before\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "."], capture_output=True, check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-m", "seed"], capture_output=True, check=True)
        from .agent_proposals import git_head

        base = git_head(repo)
        target.write_text("after\n", encoding="utf-8")
        diff = subprocess.run(
            ["git", "-C", str(repo), "diff"], capture_output=True, text=True, check=True,
        ).stdout
        subprocess.run(["git", "-C", str(repo), "checkout", "--", "sample.txt"], capture_output=True, check=True)
        proposal = AgentProposalStore(repo).record(
            run_id="eval-proposal", base_revision=base or "", diffs={"implementer": diff},
            success=True, subtask_results=[{"success": True}],
        )
        applied = AgentProposalStore(repo).apply(proposal.run_id)
        staged = subprocess.run(
            ["git", "-C", str(repo), "diff", "--cached", "--", "sample.txt"],
            capture_output=True, text=True, check=True,
        ).stdout
        passed = proposal.verified and applied.roles == ("implementer",) and "+after" in staged
    except (OSError, subprocess.CalledProcessError, ValueError):
        passed = False
    return PlatformEvalOutcome("worktree proposal gate", passed, "verified patches stage only after base and clean-tree checks")


def _fleet_case(root: Path) -> PlatformEvalOutcome:
    profiles = (
        AgentProfile("researcher", "research", "research", ("research",)),
        AgentProfile("implementer", "implement", "implement", ("implementation",), tier="write"),
        AgentProfile("reviewer", "review", "review", ("review",)),
        AgentProfile("tester", "test", "test", ("testing",), tier="test"),
        AgentProfile("payments-security-auditor", "audit", "audit", ("payments", "security")),
    )
    try:
        plan = plan_fleet(
            "audit payment security", profiles,
            core_keys=("researcher", "implementer", "reviewer", "tester"),
            repository=RepositorySignals(tags=("payments", "security")),
            write=True, max_profiles=5, max_parallel=2,
        )
        FleetPlanStore(root).save(plan)
        restored = FleetPlanStore(root).load(plan.id)
        phases = [wave.phase for wave in restored.waves]
        passed = (
            len(restored.members) == 5
            and all(wave.parallelism <= 2 for wave in restored.waves)
            and phases == ["research", "implementation", "acceptance"]
        )
    except ValueError:
        passed = False
    return PlatformEvalOutcome("fleet planning", passed, "large specialist sets become bounded persisted waves")
