"""Run and rank isolated competing agent attempts without hidden merge logic.

Trial selection is deliberately evidence-weighted. A promising answer cannot win
just because every sub-agent returned a cheerful paragraph: it must have
successful role outcomes, no failed handoff contract, and no secrets detected in
the added candidate diff. The winner remains a proposal, never an auto-merge.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
from dataclasses import dataclass
from typing import Any, Callable, Iterable


@dataclass(frozen=True)
class TrialCandidate:
    name: str
    roster: str


@dataclass(frozen=True)
class TrialResult:
    name: str
    roster: str
    success: bool
    verifier_passed: bool
    security_findings: int = 0
    quality: float = 0.0
    diff_digest: str = ""
    contract_status: str = "unknown"
    error: str | None = None


@dataclass(frozen=True)
class TrialDecision:
    winner: TrialResult | None
    rejected: tuple[TrialResult, ...]


def run_trials(
    candidates: Iterable[TrialCandidate],
    runner: Callable[[TrialCandidate], Any],
    *, max_parallel: int = 2,
) -> tuple[TrialResult, ...]:
    """Execute supplied candidates concurrently; each runner owns its worktree."""
    items = tuple(candidates)
    if not 1 <= len(items) <= 8:
        raise ValueError("trials require between one and eight candidates")
    if not 1 <= max_parallel <= len(items):
        raise ValueError("trial max_parallel must be between one and candidate count")
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_parallel) as pool:
        futures = {pool.submit(runner, candidate): candidate for candidate in items}
        results: list[TrialResult] = []
        for future, candidate in futures.items():
            try:
                outcome = future.result()
                subtask_results = getattr(outcome, "subtask_results", []) or []
                contract_status = _contract_status(outcome)
                verified = bool(getattr(outcome, "success", False)) and all(
                    bool(result.get("success", False)) for result in subtask_results
                ) and contract_status != "failed"
                diff = _candidate_diff(outcome)
                security_findings = _security_findings(diff)
                results.append(TrialResult(
                    candidate.name, candidate.roster, bool(getattr(outcome, "success", False)), verified,
                    security_findings=security_findings,
                    quality=_quality(outcome, contract_status=contract_status),
                    diff_digest=hashlib.sha256(diff.encode()).hexdigest(),
                    contract_status=contract_status,
                    error=getattr(outcome, "error", None),
                ))
            except Exception as exc:  # noqa: BLE001 - one candidate cannot hide its failure
                results.append(TrialResult(candidate.name, candidate.roster, False, False, error=f"{type(exc).__name__}: {exc}"))
    return tuple(results)


def choose_trial(results: Iterable[TrialResult]) -> TrialDecision:
    """Select only a fully verified, finding-free trial; never merge automatically."""
    rows = tuple(results)
    acceptable = [row for row in rows if row.success and row.verifier_passed and row.security_findings == 0]
    winner = max(acceptable, key=lambda row: (row.quality, row.name), default=None)
    return TrialDecision(winner, tuple(row for row in rows if row is not winner))


def _candidate_diff(outcome: Any) -> str:
    """Return every attributable patch, not only a legacy aggregate diff."""
    diffs = getattr(outcome, "worktree_diffs", None)
    if isinstance(diffs, dict):
        chunks = [str(value) for _role, value in sorted(diffs.items()) if value]
        if chunks:
            return "\n".join(chunks)
    return str(getattr(outcome, "diff", "") or "")


def _contract_status(outcome: Any) -> str:
    handoffs = getattr(outcome, "handoffs", None)
    if not isinstance(handoffs, dict):
        return "unknown"
    contract = handoffs.get("contract")
    if not isinstance(contract, dict):
        return "unknown"
    status = contract.get("final_contract_status", "unknown")
    return status if status in {"passed", "warning", "failed", "unknown"} else "unknown"


def _security_findings(diff: str) -> int:
    """Scan only added candidate lines; never retain or expose secret values."""
    if not diff:
        return 0
    from .secret_scan import find_secrets

    added = "\n".join(
        line[1:] for line in diff.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    return len(find_secrets(added, "candidate.diff"))


def _quality(outcome: Any, *, contract_status: str) -> float:
    """Score evidence, not model prose, on a bounded and explainable scale."""
    results = getattr(outcome, "subtask_results", []) or []
    if not results:
        role_score = 1.0 if getattr(outcome, "success", False) else 0.0
    else:
        role_score = sum(bool(result.get("success", False)) for result in results) / len(results)
    contract_score = {"passed": 1.0, "warning": 0.5, "unknown": 0.25, "failed": 0.0}[contract_status]
    # Role outcomes carry most of the score; typed handoff evidence breaks ties
    # between otherwise similar candidates without inventing a quality claim.
    return round((0.75 * role_score) + (0.25 * contract_score), 4)
