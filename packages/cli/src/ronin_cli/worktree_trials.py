"""Run and rank isolated competing agent attempts without hidden merge logic."""
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
                verified = bool(getattr(outcome, "success", False)) and all(
                    bool(result.get("success", False)) for result in subtask_results
                )
                diff = str(getattr(outcome, "diff", "") or "")
                results.append(TrialResult(
                    candidate.name, candidate.roster, bool(getattr(outcome, "success", False)), verified,
                    quality=_quality(outcome), diff_digest=hashlib.sha256(diff.encode()).hexdigest(),
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


def _quality(outcome: Any) -> float:
    results = getattr(outcome, "subtask_results", []) or []
    if not results:
        return 1.0 if getattr(outcome, "success", False) else 0.0
    return sum(bool(result.get("success", False)) for result in results) / len(results)
