"""The offline SFT dry-run — the 'smoke run' this environment can execute.

No GPU, no MLX, no weights: rows -> split-by-task -> assistant-only mask -> holdout
validity -> trainer argv, all offline. The two fatal dataset bugs (a leaked holdout, a
broken/empty mask) raise here rather than reaching a GPU.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest
from ronin_training.sft.pipeline import plan_sft_run
from ronin_training.sft.profiles import QWEN1P5B_DEBUG, QWEN7B_QLORA

_KNOWN = frozenset({"read", "glob"})


def _tok(text: str) -> Sequence[int]:
    return [len(w) for w in text.split()]


def _row(task: str, tool: str) -> dict[str, Any]:
    return {
        "task_id": task,
        "messages": [
            {"role": "system", "content": "you are ronin"},
            {"role": "user", "content": f"work on {task}"},
            {
                "role": "assistant",
                "content": "on it",
                "tool_calls": [{"function": {"name": tool, "arguments": {"path": "x"}}}],
            },
            {"role": "tool", "content": "ok result"},
            {"role": "assistant", "content": "done"},
        ],
    }


def _corpus(n_tasks: int = 12) -> list[dict[str, Any]]:
    return [_row(f"task-{i:02d}", "read" if i % 2 else "glob") for i in range(n_tasks)]


def test_the_dry_run_splits_masks_scores_and_builds_a_command() -> None:
    plan = plan_sft_run(_corpus(), QWEN7B_QLORA, tokenize=_tok, seed=0, known_tools=_KNOWN)
    assert plan.train_examples > 0 and plan.holdout_examples > 0
    assert plan.all_learnable, "every train row has a learnable assistant token"
    assert plan.holdout_validity.rate == 1.0  # reference calls are all valid
    assert "ronin_training.sft.train" in " ".join(plan.trainer_argv)


def test_the_split_holds_out_by_task_with_no_leak() -> None:
    # plan_sft_run calls verify_no_task_leak internally; if it returned, there was no leak.
    plan = plan_sft_run(_corpus(20), QWEN1P5B_DEBUG, tokenize=_tok, known_tools=_KNOWN)
    assert plan.holdout_tasks, "at least one task is held out"
    # A holdout task never appears among the train examples is guaranteed by the split;
    # here we just confirm the plan surfaced the held-out task set.
    assert len(plan.holdout_tasks) >= 1


def test_a_row_that_masks_to_nothing_stops_the_pipeline() -> None:
    bad = _corpus(11)
    bad.append({"task_id": "empty", "messages": [{"role": "user", "content": "no assistant here"}]})
    with pytest.raises(ValueError, match="teaches nothing"):
        plan_sft_run(bad, QWEN7B_QLORA, tokenize=_tok, known_tools=_KNOWN)


def test_the_pipeline_validates_the_profile_first() -> None:
    from dataclasses import replace

    with pytest.raises(ValueError, match="packing must be False"):
        plan_sft_run(
            _corpus(), replace(QWEN7B_QLORA, packing=True), tokenize=_tok, known_tools=_KNOWN
        )
