"""``python -m ronin_training.sft.train`` — the SFT training entrypoint.

Ties the SFT layer into one runnable script: it loads a profile
(:class:`ronin_training.sft.profiles.SFTProfile`), **validates the data offline** (splits by
task, refuses a leaked holdout or a row that masks to nothing, scores holdout tool-call
validity), and then drives the real trainer — trl/peft for the 7b lane, ``mlx_lm.lora`` for
the 1.5b lane.

``--check`` is the half this environment can run: it does the whole offline gate and prints
the resolved recipe **without importing torch, trl, or mlx** — the same "safe anywhere"
contract ``training/cuda/train_cuda.py --check`` keeps, and the offline smoke the SFT
acceptance asks for. The heavy imports live past that gate, so a box with no GPU/MLX gets a
clean, actionable error ("install …") instead of an ``ImportError`` traceback, and CI can
prove the script and its data are runnable without a training stack.

The masking (:mod:`ronin_training.sft.masking`) is applied by the trainer's collator at
token time using the assistant spans; ``--check`` proves the *row-level* precondition (every
train row has a learnable assistant turn) that the token mask depends on.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ronin_training.adapter.dataset import (
    examples_from_rows,
    read_jsonl,
    split_by_task,
    verify_no_task_leak,
)
from ronin_training.sft.eval_hook import ValidityReport, calls_from_rows, tool_validity
from ronin_training.sft.masking import learnable_row
from ronin_training.sft.profiles import SFTProfile


class TrainError(RuntimeError):
    """A run that cannot proceed, with the reason and (where possible) the fix."""


@dataclass(frozen=True, slots=True)
class CheckReport:
    """What ``--check`` established: a validated split and the recipe that would run."""

    profile_name: str
    lane: str
    train_examples: int
    holdout_examples: int
    holdout_tasks: tuple[str, ...]
    holdout_validity: ValidityReport
    recipe: tuple[str, ...]

    def render(self) -> str:
        rate = "n/a (no holdout tool calls)" if self.holdout_validity.rate is None else (
            f"{self.holdout_validity.rate:.3f} ({self.holdout_validity.valid}/"
            f"{self.holdout_validity.attempts})"
        )
        return "\n".join(
            [
                f"sft check: profile={self.profile_name} lane={self.lane}",
                f"  train examples : {self.train_examples}",
                f"  holdout examples: {self.holdout_examples} over {len(self.holdout_tasks)} task(s)",
                f"  holdout tool-call validity (reference): {rate}",
                "  the >0.97 gate is decided on the GPU run's own completions, not these rows",
                f"  would run: {' '.join(self.recipe)}",
            ]
        )


def check_run(
    profile: SFTProfile,
    rows: Sequence[Mapping[str, Any]],
    *,
    config_path: str,
    data_dir: str,
    out_dir: str,
    seed: int,
    fraction: float,
) -> CheckReport:
    """The offline gate: validate profile + split + learnability, then build the recipe.

    Raises :class:`TrainError` on the conditions that must never reach a GPU — an invalid
    profile, a leaked holdout, or a train row that would mask to nothing.
    """
    profile.validate()
    examples = examples_from_rows(rows, kind="sft")
    split = split_by_task(examples, fraction=fraction, seed=seed)
    verify_no_task_leak(split)

    unlearnable = [ex.example_id for ex in split.train if not learnable_row(ex.row)]
    if unlearnable:
        raise TrainError(
            f"{len(unlearnable)} train row(s) have no learnable assistant turn and would "
            f"mask to nothing (first: {unlearnable[0]}); fix the harvest, do not train on them"
        )

    validity = tool_validity(calls_from_rows([ex.row for ex in split.holdout]))
    recipe = profile.run_command(config_path, data_dir, out_dir)
    return CheckReport(
        profile_name=profile.name,
        lane=profile.lane,
        train_examples=len(split.train),
        holdout_examples=len(split.holdout),
        holdout_tasks=split.holdout_tasks,
        holdout_validity=validity,
        recipe=tuple(recipe),
    )


def _run_real(profile: SFTProfile, data_dir: str, out_dir: str) -> int:
    """Drive the actual trainer. Heavy imports are lazy so ``--check`` never needs them."""
    if profile.lane == "mlx":
        argv = profile.mlx_lora_argv(data_dir, out_dir)
        try:
            return subprocess.run(argv, check=False).returncode
        except FileNotFoundError as exc:  # pragma: no cover - needs a box without mlx on PATH
            raise TrainError(
                "the mlx lane needs mlx-lm on an Apple-silicon machine: "
                "pip install mlx-lm, then re-run (this is the 1.5b debug lane)"
            ) from exc
    try:  # pragma: no cover - the peft path needs a GPU box with torch+trl
        import torch  # type: ignore[import-not-found]  # noqa: F401
        from trl import SFTConfig, SFTTrainer  # type: ignore[import-not-found]  # noqa: F401
    except ImportError as exc:  # pragma: no cover
        raise TrainError(
            "the 7b peft lane needs a GPU box with torch + trl: "
            "pip install 'ronin-training[cuda]' (torch, trl, peft, bitsandbytes), then re-run"
        ) from exc
    raise TrainError(  # pragma: no cover - reached only where the stack is present
        "the peft training loop runs on the GPU box; wire this profile into "
        "training/cuda/train_cuda.py's SFTTrainer there. --check is the offline gate."
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ronin_training.sft.train", description=__doc__)
    parser.add_argument("--config", required=True, help="an SFT profile yaml")
    parser.add_argument("--data", required=True, help="a harvested SFT rows .jsonl")
    parser.add_argument("--out", default="training/adapters/sft", help="adapter output dir")
    parser.add_argument("--lane", default="", choices=["", "peft", "mlx"], help="override the profile lane")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--fraction", type=float, default=0.10, help="holdout fraction, by task")
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate the run offline and print the recipe; imports no training stack",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        profile = SFTProfile.load(args.config)
        if args.lane:
            from dataclasses import replace

            profile = replace(profile, lane=args.lane)
        rows = list(read_jsonl(Path(args.data)))
        report = check_run(
            profile,
            rows,
            config_path=args.config,
            data_dir=args.data,
            out_dir=args.out,
            seed=args.seed,
            fraction=args.fraction,
        )
    except (TrainError, ValueError, OSError) as exc:
        print(f"sft train: {exc}", file=sys.stderr)
        return 1

    print(report.render())
    if args.check:
        return 0
    try:
        return _run_real(profile, args.data, args.out)
    except TrainError as exc:
        print(f"sft train: {exc}", file=sys.stderr)
        return 1


__all__ = ["CheckReport", "TrainError", "check_run", "main"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
