"""Named health checks aggregated worst-wins.

Register callables that return a :class:`HealthResult`. Running the registry
executes each check and folds the results into an overall status where the
worst component wins (down > degraded > ok). Fail-closed: a check that raises
counts as ``down``, and an empty registry reports ``down`` (nothing has been
proven healthy). Deterministic: the caller supplies ``now``.
"""

from __future__ import annotations

from enum import Enum
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field

HealthCheck = Callable[[], "HealthResult"]


class HealthStatus(str, Enum):
    ok = "ok"
    degraded = "degraded"
    down = "down"


# Severity ranking — higher is worse. Used to pick the overall status.
_SEVERITY: dict[HealthStatus, int] = {
    HealthStatus.ok: 0,
    HealthStatus.degraded: 1,
    HealthStatus.down: 2,
}


class HealthResult(BaseModel):
    """The outcome of a single named check."""

    model_config = ConfigDict(extra="forbid")

    name: str
    status: HealthStatus
    detail: str = ""


class HealthReport(BaseModel):
    """Aggregate view: overall status plus each component result."""

    model_config = ConfigDict(extra="forbid")

    status: HealthStatus
    components: tuple[HealthResult, ...] = ()
    ts: str = ""

    def worst(self) -> HealthStatus:
        return self.status


def aggregate(results: tuple[HealthResult, ...]) -> HealthStatus:
    """Worst-wins fold. Empty input is ``down`` (fail-closed)."""
    if not results:
        return HealthStatus.down
    return max(results, key=lambda r: _SEVERITY[r.status]).status


class HealthRegistry:
    """Holds named checks and runs them into a :class:`HealthReport`."""

    def __init__(self) -> None:
        self._checks: dict[str, HealthCheck] = {}

    def register(self, name: str, check: HealthCheck) -> None:
        if not name or not name.strip():
            raise ValueError("health check name must be non-empty")
        if name in self._checks:
            raise ValueError(f"health check {name!r} already registered")
        self._checks[name] = check

    def unregister(self, name: str) -> None:
        self._checks.pop(name, None)

    def names(self) -> tuple[str, ...]:
        return tuple(self._checks)

    def run(self, *, now: str = "") -> HealthReport:
        results: list[HealthResult] = []
        for name, check in self._checks.items():
            try:
                res = check()
            except Exception as exc:  # fail-closed: a broken check is "down"
                res = HealthResult(
                    name=name,
                    status=HealthStatus.down,
                    detail=f"check raised: {type(exc).__name__}: {exc}",
                )
            # Guard against a check that reports someone else's name.
            if res.name != name:
                res = res.model_copy(update={"name": name})
            results.append(res)

        components = tuple(results)
        return HealthReport(
            status=aggregate(components),
            components=components,
            ts=now,
        )
