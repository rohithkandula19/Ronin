"""Explainable, evidence-led model routing for heterogeneous agent teams."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from .agent_catalog import AgentProfile


@dataclass(frozen=True)
class ModelCandidate:
    spec: str
    quality: float = 0.5
    cost: float = 0.5
    latency: float = 0.5

    def __post_init__(self) -> None:
        if not self.spec.strip():
            raise ValueError("model candidate spec must not be empty")
        if not all(0.0 <= value <= 1.0 for value in (self.quality, self.cost, self.latency)):
            raise ValueError("model candidate scores must be between 0 and 1")


@dataclass(frozen=True)
class RoutingDecision:
    role: str
    spec: str
    score: float
    reason: str


def parse_candidates(spec: str) -> tuple[ModelCandidate, ...]:
    """Parse ``provider:model[:quality:cost:latency]`` candidate entries."""
    candidates: list[ModelCandidate] = []
    for raw in spec.split(","):
        parts = [part.strip() for part in raw.split(":")]
        if not parts or not parts[0]:
            continue
        model = ":".join(parts[:2]) if len(parts) > 1 and not _numeric(parts[1]) else parts[0]
        numbers = parts[2:] if len(parts) > 1 and not _numeric(parts[1]) else parts[1:]
        values = [float(value) for value in numbers if value]
        if len(values) > 3:
            raise ValueError("candidate supports at most quality, cost, and latency scores")
        candidates.append(ModelCandidate(model, *(values + [0.5] * (3 - len(values)))))
    if not candidates:
        raise ValueError("at least one model candidate is required")
    return tuple(candidates)


def route_profiles(
    profiles: Iterable[AgentProfile],
    candidates: Iterable[ModelCandidate],
    observations: Mapping[str, Mapping[str, object]] | None = None,
) -> tuple[RoutingDecision, ...]:
    """Route each role using measured quality/cost/latency and local health data."""
    options = tuple(candidates)
    if not options:
        raise ValueError("routing needs at least one model candidate")
    observed = observations or {}
    decisions: list[RoutingDecision] = []
    for profile in profiles:
        ranked = sorted(
            ((_score(profile.tier, candidate, observed), candidate) for candidate in options),
            key=lambda pair: (pair[0], pair[1].spec), reverse=True,
        )
        score, winner = ranked[0]
        health = _health(winner.spec, observed)
        decisions.append(RoutingDecision(
            role=profile.key,
            spec=winner.spec,
            score=round(score, 3),
            reason=f"{profile.tier} priority; observed health={health}",
        ))
    return tuple(decisions)


def roster_from_decisions(decisions: Iterable[RoutingDecision]) -> str:
    return ",".join(f"{decision.role}={decision.spec}" for decision in decisions)


def _score(tier: str, candidate: ModelCandidate, observations: Mapping[str, Mapping[str, object]]) -> float:
    health = _health(candidate.spec, observations)
    reliability = {"healthy": 1.0, "degraded": 0.0, "unknown": 0.5}[health]
    if tier == "write":
        return .55 * candidate.quality + .15 * (1 - candidate.cost) + .10 * (1 - candidate.latency) + .20 * reliability
    if tier == "test":
        return .45 * candidate.quality + .20 * (1 - candidate.cost) + .15 * (1 - candidate.latency) + .20 * reliability
    return .25 * candidate.quality + .35 * (1 - candidate.cost) + .20 * (1 - candidate.latency) + .20 * reliability


def _health(spec: str, observations: Mapping[str, Mapping[str, object]]) -> str:
    exact = observations.get(spec)
    if isinstance(exact, Mapping):
        return "healthy" if exact.get("status") == "healthy" else "degraded"
    provider = spec.split(":", 1)[0]
    seen = [value for key, value in observations.items() if key.split(":", 1)[0] == provider]
    if not seen:
        return "unknown"
    return "healthy" if all(value.get("status") == "healthy" for value in seen) else "degraded"


def _numeric(value: str) -> bool:
    try:
        float(value)
        return True
    except ValueError:
        return False
