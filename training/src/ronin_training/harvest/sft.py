"""Successful trajectories -> SFT examples, one per assistant turn, with real counts.

An example is ``(system + tools + history) -> (assistant turn)``: the prompt is the
run's own state at that point, rendered by the single renderer in :mod:`.prompt`,
and the target is the turn the passing run actually took.

The cap is explicit because more is not better here. The adapter targets a 1.5B
model; past roughly eight thousand examples the extra rows buy nothing and cost
training time on hardware that is free but slow. ``SftConfig.max_examples`` is the
knob, ``TARGET_MIN_EXAMPLES``/``TARGET_MAX_EXAMPLES`` are the band the user asked for,
and :class:`HarvestCounts` reports what the material actually yielded — which may
be far below the band. The pipeline states the shortfall; it never pads to the target.
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .augment import augment
from .filters import TURN_BUDGET_FACTOR, FilterCounts, TaskBudget, select
from .prompt import PromptPayload, assistant_message, render_prefix
from .provenance import Provenance
from .trajectory import Step, Trajectory

#: The band the user specified for a 1.5B adapter. Not a measurement — a budget.
TARGET_MIN_EXAMPLES = 3_000
TARGET_MAX_EXAMPLES = 8_000

DEFAULT_MAX_EXAMPLES = TARGET_MAX_EXAMPLES


@dataclass(frozen=True, slots=True)
class SftConfig:
    """Every extraction choice, in one value, so a run is reproducible from it."""

    #: Hard cap on emitted examples. Reached by truncation, never by sampling, so
    #: two runs of the same material emit the same prefix of the same rows.
    max_examples: int = DEFAULT_MAX_EXAMPLES
    #: Perturbed copies per surviving trajectory, on top of the unperturbed one.
    augment_copies: int = 0
    seed: int = 0
    turn_budget_factor: float = TURN_BUDGET_FACTOR
    #: Turns that call no tool teach prose, not protocol. Kept out by default: the
    #: adapter's job is tool syntax, gates, recovery and planning, and a corpus
    #: padded with "here is my summary" turns spends its budget on the one thing
    #: the base model is already fine at.
    require_tool_call: bool = True
    #: Drop rows whose rendered prompt+target is byte-identical to one already
    #: emitted. Augmented copies of one trajectory collide often at step 0.
    deduplicate: bool = True

    def __post_init__(self) -> None:
        if self.max_examples <= 0:
            raise ValueError("SftConfig.max_examples must be > 0")
        if self.augment_copies < 0:
            raise ValueError("SftConfig.augment_copies must be >= 0")
        if self.turn_budget_factor <= 0:
            raise ValueError("SftConfig.turn_budget_factor must be > 0")


@dataclass(frozen=True, slots=True)
class SftExample:
    """One training row plus the provenance that makes it auditable."""

    payload: PromptPayload
    completion: Mapping[str, Any]
    provenance: Provenance

    def as_row(self) -> dict[str, Any]:
        """The trainer-facing row: ``{"messages": [...], "tools": [...]}``.

        The same shape ``dataset_builder`` exports and ``train_cuda.py`` renders,
        so these rows can be concatenated with the volume corpus without a converter.
        """
        return self.payload.with_completion(self.completion).as_row()

    def as_record(self) -> dict[str, Any]:
        """The row with its ``meta``. Both trainers ignore unknown keys, so this is
        what gets written by default — a dataset that drops its own provenance to
        satisfy a trainer that never asked is a dataset nobody can defend later."""
        row = self.as_row()
        row["meta"] = self.provenance.as_dict()
        return row

    def digest(self) -> str:
        return self.payload.with_completion(self.completion).digest()


@dataclass(frozen=True, slots=True)
class HarvestCounts:
    """What the material yielded, and what each stage removed. Real numbers only."""

    filters: FilterCounts
    trajectories_used: int = 0
    turns_seen: int = 0
    dropped_no_tool_call: int = 0
    dropped_duplicate: int = 0
    dropped_over_cap: int = 0
    emitted: int = 0
    emitted_augmented: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "filters": self.filters.as_dict(),
            "trajectories_used": self.trajectories_used,
            "turns_seen": self.turns_seen,
            "dropped_no_tool_call": self.dropped_no_tool_call,
            "dropped_duplicate": self.dropped_duplicate,
            "dropped_over_cap": self.dropped_over_cap,
            "emitted": self.emitted,
            "emitted_augmented": self.emitted_augmented,
        }

    def shortfall(self, *, target: int = TARGET_MIN_EXAMPLES) -> int:
        """How far below the target band the yield landed. 0 when inside or above."""
        return max(0, target - self.emitted)

    def summary(self) -> str:
        lines = [
            self.filters.summary(),
            f"{self.turns_seen} assistant turn(s) from {self.trajectories_used} "
            f"trajectory(ies) -> {self.emitted} example(s) "
            f"({self.emitted_augmented} augmented)",
            f"turn drops: {self.dropped_no_tool_call} no tool call, "
            f"{self.dropped_duplicate} duplicate, {self.dropped_over_cap} over cap",
        ]
        gap = self.shortfall()
        if gap:
            lines.append(
                f"{gap} example(s) below the {TARGET_MIN_EXAMPLES}-example target: "
                f"the material is thin, not padded"
            )
        return "\n".join(lines)


def _teaches_protocol(step: Step, *, require_tool_call: bool) -> bool:
    return bool(step.calls) or not require_tool_call


def extract(
    trajectories: Sequence[Trajectory], *, config: SftConfig | None = None
) -> tuple[tuple[SftExample, ...], HarvestCounts, Mapping[str, TaskBudget]]:
    """Filter, expand, render. The whole SFT side of the pipeline in one call."""
    cfg = config or SftConfig()
    kept, filter_counts, budgets = select(trajectories, factor=cfg.turn_budget_factor)

    examples: list[SftExample] = []
    seen: set[str] = set()
    turns_seen = 0
    no_call = 0
    duplicate = 0
    over_cap = 0
    augmented_emitted = 0

    for traj in kept:
        for variant_index, variant in enumerate(_variants(traj, cfg)):
            is_augmented = variant_index > 0
            seed = cfg.seed + variant_index if is_augmented else -1
            for step_index, step in enumerate(variant.steps):
                turns_seen += 1
                if not _teaches_protocol(step, require_tool_call=cfg.require_tool_call):
                    no_call += 1
                    continue
                example = SftExample(
                    payload=render_prefix(variant, step_index),
                    completion=assistant_message(step),
                    provenance=Provenance(
                        task_id=variant.task_id,
                        run_id=variant.run_id,
                        model=variant.model,
                        source=variant.source,
                        turn_index=step_index,
                        augmented=is_augmented,
                        augment_seed=seed if is_augmented else -1,
                    ),
                )
                if cfg.deduplicate:
                    digest = example.digest()
                    if digest in seen:
                        duplicate += 1
                        continue
                    seen.add(digest)
                if len(examples) >= cfg.max_examples:
                    over_cap += 1
                    continue
                examples.append(example)
                if is_augmented:
                    augmented_emitted += 1

    counts = HarvestCounts(
        filters=filter_counts,
        trajectories_used=len(kept),
        turns_seen=turns_seen,
        dropped_no_tool_call=no_call,
        dropped_duplicate=duplicate,
        dropped_over_cap=over_cap,
        emitted=len(examples),
        emitted_augmented=augmented_emitted,
    )
    return tuple(examples), counts, budgets


def _variants(traj: Trajectory, cfg: SftConfig) -> tuple[Trajectory, ...]:
    """The original plus ``augment_copies`` perturbed copies, in a fixed order."""
    copies = tuple(
        augment(traj, seed=cfg.seed + i + 1, run_id_suffix=f"aug{i + 1}")
        for i in range(cfg.augment_copies)
    )
    return (traj, *copies)


def write_jsonl(path: Path, examples: Iterable[SftExample], *, with_meta: bool = True) -> int:
    """Write rows as JSONL. Returns the count actually written.

    ``with_meta=False`` emits the bare ``{"messages", "tools"}`` form for a trainer
    that rejects unknown keys. Provenance is then unrecoverable from the file, so
    the caller has to ask for that explicitly.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for example in examples:
            row = example.as_record() if with_meta else example.as_row()
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            written += 1
    return written


def rebase_cap(config: SftConfig, max_examples: int) -> SftConfig:
    """A copy of ``config`` with a different cap. The cap is the knob operators turn."""
    return replace(config, max_examples=max_examples)


__all__ = [
    "DEFAULT_MAX_EXAMPLES",
    "TARGET_MAX_EXAMPLES",
    "TARGET_MIN_EXAMPLES",
    "HarvestCounts",
    "SftConfig",
    "SftExample",
    "extract",
    "rebase_cap",
    "write_jsonl",
]
