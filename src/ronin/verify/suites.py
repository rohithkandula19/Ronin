"""Required vs optional suites: what fails the gate, and what only informs it.

The existing runner has one wall — the change-scoped required tests — and the end-of-task
full suite. This adds the tier the roadmap asks for: *optional* suites (a linter, a
type-checker, a slow integration pass) that run and report but do **not** fail the gate.
The distinction is the point and is kept honest: an optional failure is surfaced, never
hidden, and never silently promoted to a blocker — but it also never turns a green task
red. Only a required failure does that.

Pure aggregation over results the caller already has, so it is tested with hand-built
:class:`SuiteResult` values and runs nothing itself.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum


class SuiteTier(StrEnum):
    """Whether a suite's failure blocks the task or only informs it."""

    REQUIRED = "required"
    OPTIONAL = "optional"


@dataclass(frozen=True, slots=True)
class SuiteResult:
    """One suite that ran, its tier, and whether it passed."""

    name: str
    tier: SuiteTier
    passed: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class TieredOutcome:
    """The aggregate: the gate is decided by required suites alone."""

    results: tuple[SuiteResult, ...] = ()

    @property
    def required_failures(self) -> tuple[SuiteResult, ...]:
        return tuple(r for r in self.results if r.tier is SuiteTier.REQUIRED and not r.passed)

    @property
    def optional_failures(self) -> tuple[SuiteResult, ...]:
        return tuple(r for r in self.results if r.tier is SuiteTier.OPTIONAL and not r.passed)

    @property
    def gate_passed(self) -> bool:
        """True when no *required* suite failed. Optional failures never fail the gate."""
        return not self.required_failures

    def summary(self) -> str:
        """One honest line: the gate verdict, with optional failures named, not buried."""
        verdict = "gate passed" if self.gate_passed else "gate FAILED"
        parts = [f"{verdict} ({len(self.results)} suite(s))"]
        if self.required_failures:
            parts.append("required failed: " + ", ".join(r.name for r in self.required_failures))
        if self.optional_failures:
            parts.append(
                "optional failed (advisory): " + ", ".join(r.name for r in self.optional_failures)
            )
        return " · ".join(parts)


def aggregate(results: Sequence[SuiteResult]) -> TieredOutcome:
    """Fold suite results into a :class:`TieredOutcome`."""
    return TieredOutcome(results=tuple(results))


__all__ = ["SuiteResult", "SuiteTier", "TieredOutcome", "aggregate"]
