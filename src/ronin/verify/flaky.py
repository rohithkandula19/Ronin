"""Flaky-test detection: a test that changes its answer on identical runs.

A test whose pass/fail flips across runs of the *same* code is worse than a failing one:
it poisons the verify signal — a green that means nothing — and in the RL environment it
hands out reward for noise. This classifies a target from repeated outcomes of the same
code, so a mixed result is named as flakiness rather than taken at face value.

Pure by construction: the thing that runs the test is an injected callable, so detection
is exercised with a scripted sequence of results and never runs a real test. The honest
limit is stated plainly — N runs can only *observe* flakiness, never prove its absence, so
a ``STABLE_PASS`` at three attempts is "no flake seen in three," not "not flaky."
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum

#: Default number of identical runs. Three is the smallest that can show a flake while
#: keeping a stable verdict cheap; the caller raises it when it wants more confidence.
DEFAULT_ATTEMPTS = 3


class Stability(StrEnum):
    """What repeated runs of the same code revealed about a test."""

    STABLE_PASS = "stable_pass"
    STABLE_FAIL = "stable_fail"
    FLAKY = "flaky"


def classify(results: Sequence[bool]) -> Stability:
    """All pass → stable pass; all fail → stable fail; a mix → flaky.

    Raises on an empty sequence: "no runs" is a caller bug, not a stability verdict.
    """
    if not results:
        raise ValueError("classify needs at least one run")
    if all(results):
        return Stability.STABLE_PASS
    if not any(results):
        return Stability.STABLE_FAIL
    return Stability.FLAKY


@dataclass(frozen=True, slots=True)
class FlakeResult:
    """One target's repeated outcomes and the verdict drawn from them."""

    target: str
    runs: tuple[bool, ...]
    stability: Stability

    @property
    def is_flaky(self) -> bool:
        return self.stability is Stability.FLAKY

    @property
    def passes(self) -> int:
        return sum(self.runs)

    def summary(self) -> str:
        return (
            f"{self.target}: {self.stability.value} "
            f"({self.passes}/{len(self.runs)} passed over {len(self.runs)} runs)"
        )


def probe(target: str, run: Callable[[], bool], *, attempts: int = DEFAULT_ATTEMPTS) -> FlakeResult:
    """Run ``target`` ``attempts`` times through ``run`` and classify the outcomes.

    ``run`` returns ``True`` for a pass. It is called exactly ``attempts`` times against the
    same code — that identity is the whole premise, so the caller must not mutate the tree
    between runs. Fewer than two attempts cannot reveal a flake, so it is refused.
    """
    if attempts < 2:
        raise ValueError("flaky detection needs at least 2 attempts")
    runs = tuple(run() for _ in range(attempts))
    return FlakeResult(target=target, runs=runs, stability=classify(runs))


__all__ = ["DEFAULT_ATTEMPTS", "FlakeResult", "Stability", "classify", "probe"]
