"""The offline SFT dry-run: harvested rows → split → masked examples → trainer command.

This is the "smoke run" this environment *can* execute: everything the real 1.5b/7b run
does up to the point weights are loaded, with no GPU, no MLX, and no model download. It:

1. lifts task ids and splits by task (never by example) with a held-out 10%
   (:mod:`ronin_training.adapter.dataset`);
2. builds the assistant-only ``-100`` label mask for every train row
   (:mod:`ronin_training.sft.masking`) — a row that masks to nothing raises here, so a
   dataset that teaches nothing cannot reach the trainer;
3. scores tool-call validity on the holdout (:mod:`ronin_training.sft.eval_hook`);
4. builds the trainer argv the profile implies (:mod:`ronin_training.sft.profiles`).

What it does **not** do is train — that is the GPU step, and the >0.97 holdout gate is
decided there on the trained model's outputs. The value of running steps 1–4 offline is
that the two failures that silently ruin an agent SFT — a leaked holdout and a broken mask
— are caught here, deterministically, before any GPU time is spent.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ronin_training.adapter.dataset import (
    examples_from_rows,
    split_by_task,
    verify_no_task_leak,
)
from ronin_training.sft.eval_hook import ValidityReport, calls_from_rows, tool_validity
from ronin_training.sft.masking import LabeledExample, Tokenizer, build_labels
from ronin_training.sft.profiles import SFTProfile


@dataclass(frozen=True, slots=True)
class SFTPlan:
    """What the dry-run established: a split, a proven mask, a validity, a command."""

    train_examples: int
    holdout_examples: int
    holdout_tasks: tuple[str, ...]
    all_learnable: bool
    holdout_validity: ValidityReport
    trainer_argv: tuple[str, ...]


def plan_sft_run(
    rows: Sequence[Mapping[str, Any]],
    profile: SFTProfile,
    *,
    tokenize: Tokenizer,
    fraction: float = 0.10,
    seed: int = 0,
    known_tools: frozenset[str] | None = None,
    config_path: str = "",
    data_dir: str = "training/data/sft",
    adapter_out: str = "training/adapters/sft",
) -> SFTPlan:
    """Run the offline pipeline end to end and return the plan. Trains nothing.

    Raises on the two fatal conditions rather than reporting them: a task that leaked
    across the split (``verify_no_task_leak``) and a train row with no learnable token
    (``build_labels``). Both are dataset bugs that must never reach a GPU.
    """
    profile.validate()
    examples = examples_from_rows(rows, kind="sft")
    split = split_by_task(examples, fraction=fraction, seed=seed)
    verify_no_task_leak(split)

    labeled: list[LabeledExample] = [
        build_labels(example.row.get("messages", []), tokenize) for example in split.train
    ]
    all_learnable = all(example.learn_count > 0 for example in labeled)

    validity = tool_validity(
        calls_from_rows([example.row for example in split.holdout]), known_tools=known_tools
    )
    argv = profile.run_command(config_path, data_dir, adapter_out)
    return SFTPlan(
        train_examples=len(split.train),
        holdout_examples=len(split.holdout),
        holdout_tasks=split.holdout_tasks,
        all_learnable=all_learnable,
        holdout_validity=validity,
        trainer_argv=tuple(argv),
    )


__all__ = ["SFTPlan", "plan_sft_run"]
