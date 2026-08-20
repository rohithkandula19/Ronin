"""The DPO eval hooks: recovery-after-failure (up) and loop rate (down), on the holdout.

DPO's job is behavioural, so its gate is behavioural: after the pass, the model should
**recover** more often when a tool fails or a call is denied, and **loop** less often. Both
are measured on the held-out tasks, on the trained model's own rollouts, on the GPU. This
module is the offline, deterministic core of that gate:

* **recovery rate** is reused verbatim from :mod:`ronin_training.adapter.metrics` — the same
  definition the SFT/eval side uses, so "recovery" means one thing in this repo, not two.
* **loop rate** is added here: the fraction of rollouts that repeat one action fingerprint
  at least :data:`LOOP_THRESHOLD` times — the stall the runtime's own detector catches, and
  the behaviour DPO's ``REPEATED_ACTION`` pairs train against.

The pass/fail comparison (recovery up, loop down, no eval-suite regression) is made on the
GPU run's numbers; the counting is here and unit-tested.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

# Recovery is defined once, in metrics; re-exported so a caller imports it from the DPO
# eval hook without the two definitions ever drifting apart.
from ronin_training.adapter.metrics import (
    RecoveryScore,
    Setback,
    TurnRecord,
    recovery_rate,
)

#: A rollout "looped" if one action fingerprint occurs at least this many times — the same
#: count ``ronin.core.loop``'s stall detector and ``harvest.negatives`` use.
LOOP_THRESHOLD: Final[int] = 3


@dataclass(frozen=True, slots=True)
class LoopRate:
    """How many holdout rollouts looped, with the denominator visible."""

    looped: int
    total: int

    def __post_init__(self) -> None:
        if self.looped > self.total or self.looped < 0:
            raise ValueError(f"impossible loop rate: {self.looped} of {self.total}")

    @property
    def rate(self) -> float | None:
        return self.looped / self.total if self.total else None


def rollout_looped(fingerprints: Sequence[str], *, threshold: int = LOOP_THRESHOLD) -> bool:
    """Whether a single rollout looped: any action fingerprint seen ``threshold``+ times."""
    if not fingerprints:
        return False
    return max(Counter(fingerprints).values()) >= threshold


def loop_rate(rollouts: Sequence[Sequence[str]], *, threshold: int = LOOP_THRESHOLD) -> LoopRate:
    """The fraction of rollouts that looped. Each rollout is its sequence of fingerprints."""
    looped = sum(1 for r in rollouts if rollout_looped(r, threshold=threshold))
    return LoopRate(looped=looped, total=len(rollouts))


__all__ = [
    "LOOP_THRESHOLD",
    "LoopRate",
    "RecoveryScore",
    "Setback",
    "TurnRecord",
    "loop_rate",
    "recovery_rate",
    "rollout_looped",
]
