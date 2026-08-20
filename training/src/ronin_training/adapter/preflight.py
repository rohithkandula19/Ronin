"""Everything that must be true before a GPU-hour is spent. One command, no ML stack.

This is the seam between the adapter pipeline and the existing free-training path
(`training/colab/train_free.ipynb`, `training/cuda/train_cuda.py`). A notebook cell
calls this and gets, in order:

1. the config, validated — including the holdout rule and the dialect tags;
2. the split, built by task and asserted leak-free, with a written manifest;
3. the exact per-backend settings the trainer should be handed, printed so they are
   in the notebook's own output and therefore in the run's record.

It deliberately stops there. It does **not** train and it does not import ``torch``,
``peft`` or ``mlx``, so it runs anywhere — including a machine with no GPU, which is
where a misconfiguration is cheapest to find.

    python -m ronin_training.adapter.preflight \\
        --config training/config/adapter_sft.yaml \\
        --rows training/data/adapter/harvested.jsonl \\
        --out training/data/adapter

Exit code 0 means "safe to train". Anything else means stop.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .config import AdapterConfig, ConfigError, TrainPass, Validation, load_config, validate
from .dataset import DatasetError, TaskSplit, read_examples, split_by_task, write_split


@dataclass(frozen=True, slots=True)
class Preflight:
    """The outcome of a preflight. ``ok`` is the only thing a caller must check."""

    config: AdapterConfig
    validation: Validation
    split: TaskSplit | None
    written: tuple[str, ...] = ()
    problems: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.validation.errors and not self.problems and self.split is not None

    @property
    def train_rows(self) -> int:
        return len(self.split.train) if self.split is not None else 0


def preflight(
    *,
    config_path: str | Path,
    rows_path: str | Path,
    out_dir: str | Path,
) -> Preflight:
    """Validate, split, and write. Never trains, never imports an ML stack."""
    cfg = load_config(config_path)
    kind = "dpo" if cfg.pass_ is TrainPass.DPO else "sft"
    problems: list[str] = []
    split: TaskSplit | None = None
    written: tuple[str, ...] = ()
    try:
        examples = read_examples(rows_path, kind=kind)
        split = split_by_task(
            examples, fraction=cfg.holdout.fraction, seed=cfg.holdout.seed
        )
        paths = write_split(split, out_dir)
        written = tuple(str(path) for path in sorted(paths.values()))
    except DatasetError as exc:
        problems.append(str(exc))
    result = validate(cfg, train_rows=len(split.train) if split is not None else None)
    return Preflight(
        config=cfg,
        validation=result,
        split=split,
        written=written,
        problems=tuple(problems),
    )


def render(result: Preflight) -> str:
    """The block a notebook cell prints. Plain text so it survives a Colab log."""
    cfg = result.config
    lines = [
        f"pass:            {cfg.pass_.value}",
        f"base model:      {cfg.base_model}  ({cfg.base_model_licence})",
        f"lora:            r={cfg.lora.rank} alpha={cfg.lora.alpha} "
        f"(mlx scale {cfg.lora.mlx_scale}) dropout={cfg.lora.dropout}",
        f"targets:         {', '.join(cfg.lora.target_modules)}",
        f"optimiser:       lr={cfg.train.learning_rate} {cfg.train.schedule} "
        f"epochs={cfg.train.epochs} seq={cfg.train.max_seq_length} "
        f"grad_checkpoint={cfg.train.grad_checkpoint}",
        f"effective batch: {cfg.train.batch_size} x {cfg.train.grad_accum} = "
        f"{cfg.train.effective_batch}",
    ]
    if cfg.dpo is not None:
        lines.append(f"dpo:             beta={cfg.dpo.beta} resume={cfg.resume_adapter}")
    lines.append(f"dialect:         {cfg.dialect_open_tag} … {cfg.dialect_close_tag}")

    if result.split is not None:
        manifest = result.split.manifest()
        lines += [
            "",
            f"holdout by:      {cfg.holdout.by} (fraction {cfg.holdout.fraction}, "
            f"seed {cfg.holdout.seed})",
            f"tasks:           {len(manifest['train_tasks'])} train / "
            f"{len(manifest['holdout_tasks'])} holdout",
            f"examples:        {manifest['train_examples']} train / "
            f"{manifest['holdout_examples']} holdout",
            f"example fraction held out: {manifest['example_fraction_held_out']:.4f}",
            f"LEAKED TASKS:    {manifest['leaked_tasks']}  (must be [])",
            f"wrote:           {list(result.written)}",
            "",
            f"mlx iterations for {result.train_rows} rows: "
            f"{cfg.iters_for(result.train_rows)}",
            "peft/trl kwargs:",
            json.dumps(cfg.to_peft_kwargs(), indent=2, sort_keys=True),
        ]
    for problem in result.problems:
        lines.append(f"DATASET  {problem}")
    rendered = result.validation.render()
    if rendered != "ok":
        lines.append(rendered)
    lines.append("")
    lines.append("PREFLIGHT OK — safe to train" if result.ok else "PREFLIGHT FAILED — stop")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--config", required=True, help="an adapter_*.yaml config")
    parser.add_argument("--rows", required=True, help="the harvested .jsonl corpus")
    parser.add_argument("--out", required=True, help="where to write the split")
    parser.add_argument(
        "--mlx-config",
        default="",
        help="also write the mlx_lm.lora -c config file here (Apple silicon lane)",
    )
    args = parser.parse_args(argv)
    try:
        result = preflight(config_path=args.config, rows_path=args.rows, out_dir=args.out)
    except ConfigError as exc:
        print(f"PREFLIGHT FAILED — {exc}")
        return 2
    print(render(result))
    if result.ok and args.mlx_config:
        from .config import dump_mlx_config

        written = dump_mlx_config(
            result.config, args.mlx_config, train_rows=result.train_rows
        )
        print(f"wrote mlx config: {written}")
    return 0 if result.ok else 1


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
