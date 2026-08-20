"""Where every emitted row came from. A dataset you cannot audit you cannot defend.

Provenance is a *value on the row*, not a log line beside it, because the question
that matters later — "is this example real, or did the pipeline invent the good
half of it?" — is asked months after the run, by someone reading the JSONL.

Two flags carry more weight than the rest:

* ``augmented`` plus ``augment_seed`` — a perturbed row is reproducible from the
  seed, so "the model learned this path name" can be tested by regenerating with
  a different seed instead of argued about.
* ``synthesized_chosen`` — the preferred side of a preference pair was *constructed
  from the violated rule* rather than taken from a passing run. Mixing the two
  silently would let a later analysis weight machine-written corrections as if a
  real agent had produced them, so the flag is mandatory and never defaulted away.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class SourceKind(StrEnum):
    """Where a trajectory came from. ``SESSION`` is opt-in; see :mod:`.sources`."""

    EVAL_SUITE = "eval_suite"
    SESSION = "session"


#: Sentinel for "not augmented". A real seed is >= 0, so -1 cannot collide with one.
NO_SEED = -1

#: Sentinel for "not a step of a trajectory" (a row assembled from a whole run).
NO_STEP = -1


@dataclass(frozen=True, slots=True)
class Provenance:
    """The audit record stamped onto one emitted example or preference pair."""

    task_id: str
    run_id: str
    #: The model that produced the trajectory this row was extracted from. Empty
    #: means the recorded transcript did not say — recorded as empty rather than
    #: guessed, because "which model taught this" is exactly what a later audit
    #: needs and a plausible-looking guess destroys the answer.
    model: str = ""
    source: SourceKind = SourceKind.EVAL_SUITE
    turn_index: int = NO_STEP
    augmented: bool = False
    augment_seed: int = NO_SEED
    #: Only meaningful on a preference pair.
    negative_class: str = ""
    synthesized_chosen: bool = False

    def __post_init__(self) -> None:
        if not self.task_id:
            raise ValueError("Provenance.task_id is required — an unattributable row")
        if not self.run_id:
            raise ValueError("Provenance.run_id is required — an unattributable row")
        if self.augmented and self.augment_seed == NO_SEED:
            raise ValueError(
                "an augmented row must carry its augment_seed, otherwise the "
                "perturbation cannot be reproduced or reversed"
            )

    def as_dict(self) -> dict[str, Any]:
        """The ``meta`` object written onto a JSONL row. Fixed key order."""
        return {
            "task_id": self.task_id,
            "run_id": self.run_id,
            "model": self.model,
            "source": str(self.source),
            "turn_index": self.turn_index,
            "augmented": self.augmented,
            "augment_seed": self.augment_seed,
            "negative_class": self.negative_class,
            "synthesized_chosen": self.synthesized_chosen,
        }


__all__ = ["NO_SEED", "NO_STEP", "Provenance", "SourceKind"]
