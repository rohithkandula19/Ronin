"""Forge training bundles and job state — the honest line between a generated
bundle and a trained model.

A `TrainingJob` starts in GENERATED: a complete, runnable QLoRA bundle exists
on disk, but NOTHING has trained. It advances only through real events a caller
reports (submitted/running/completed on a GPU host). The bundle generator
produces dataset + config + script + requirements + README + model card +
eval commands + Ollama Modelfile — but calling it never claims a training run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .provenance import ProvenanceDataset


class JobState(str, Enum):
    generated = "generated"
    submitted = "submitted"
    running = "running"
    failed = "failed"
    completed = "completed"
    evaluating = "evaluating"
    approved = "approved"
    rejected = "rejected"
    released = "released"
    rolled_back = "rolled_back"


_ALLOWED: dict[JobState, frozenset[JobState]] = {
    JobState.generated: frozenset({JobState.submitted}),
    JobState.submitted: frozenset({JobState.running, JobState.failed}),
    JobState.running: frozenset({JobState.completed, JobState.failed}),
    JobState.failed: frozenset({JobState.submitted}),
    JobState.completed: frozenset({JobState.evaluating}),
    JobState.evaluating: frozenset({JobState.approved, JobState.rejected}),
    JobState.approved: frozenset({JobState.released, JobState.rejected}),
    JobState.rejected: frozenset({JobState.evaluating}),
    JobState.released: frozenset({JobState.rolled_back}),
    JobState.rolled_back: frozenset({JobState.evaluating}),
}


class JobStateError(RuntimeError):
    pass


@dataclass
class TrainingJob:
    id: str
    method: str  # sft | lora | qlora
    base_model: str
    industry: str
    bundle_dir: str
    state: JobState = JobState.generated
    history: list[tuple[JobState, str]] = field(default_factory=list)  # (state, note)

    def advance(self, new_state: JobState, *, note: str = "") -> None:
        if new_state not in _ALLOWED[self.state]:
            raise JobStateError(
                f"job {self.id}: {self.state.value} -> {new_state.value} illegal "
                f"(allowed: {sorted(s.value for s in _ALLOWED[self.state])})"
            )
        self.history.append((new_state, note))
        self.state = new_state

    @property
    def trained(self) -> bool:
        """True only once real training completed — never for a generated bundle."""
        return self.state in {
            JobState.completed,
            JobState.evaluating,
            JobState.approved,
            JobState.released,
        }


@dataclass
class BundleConfig:
    base_model: str = "mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit"
    method: str = "qlora"
    iters: int = 400
    batch_size: int = 1
    lora_layers: int = 8
    learning_rate: float = 1e-5
    max_seq_length: int = 3072
    seed: int = 0
    industry: str = "coding"

    def estimate(self) -> dict:
        """Honest, clearly-heuristic estimates — not a promise of hardware or cost."""
        return {
            "note": "rough heuristic estimates, not a guarantee",
            "min_vram_gb_qlora_1_5b": 6,
            "recommended_ram_gb": 16,
            "approx_wallclock_minutes": max(5, self.iters // 20),
            "approx_cost_usd_cloud_gpu": round(self.iters / 400 * 1.5, 2),
        }


def generate_bundle(
    dataset: ProvenanceDataset,
    out_dir: str | Path,
    config: BundleConfig,
) -> TrainingJob:
    """Write a complete, runnable training bundle. Returns a GENERATED job.

    Only training-eligible items are written to the dataset — provenance does
    the gating. If nothing is eligible, that is an error, not an empty bundle.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    eligible = dataset.eligible()
    if not eligible:
        raise ValueError(
            "no training-eligible items — check provenance (consent, license, "
            "redaction review). Bundle refused rather than shipping an empty dataset."
        )

    # dataset.jsonl in the MLX/messages-agnostic instruction shape
    data_path = out / "dataset.jsonl"
    data_path.write_text(
        "".join(
            json.dumps({"instruction": i.instruction, "input": i.input, "output": i.output})
            + "\n"
            for i in eligible
        ),
        encoding="utf-8",
    )
    (out / "config.json").write_text(
        json.dumps(config.__dict__, indent=2, sort_keys=True), encoding="utf-8"
    )
    (out / "requirements.txt").write_text(
        "mlx-lm>=0.20\n# or, for the unsloth/GGUF path: unsloth trl peft transformers\n",
        encoding="utf-8",
    )
    (out / "train.py").write_text(_train_script(config), encoding="utf-8")
    (out / "README.md").write_text(_readme(config, len(eligible)), encoding="utf-8")
    (out / "MODEL_CARD.md").write_text(_model_card(config), encoding="utf-8")
    (out / "Modelfile").write_text(_modelfile(config), encoding="utf-8")
    (out / "estimate.json").write_text(
        json.dumps(config.estimate(), indent=2), encoding="utf-8"
    )

    return TrainingJob(
        id=f"job-{config.industry}-{config.method}-{config.seed}",
        method=config.method,
        base_model=config.base_model,
        industry=config.industry,
        bundle_dir=str(out),
        state=JobState.generated,
    )


def _train_script(c: BundleConfig) -> str:
    return f'''#!/usr/bin/env python
"""Generated QLoRA training script — RUN THIS on a machine with the hardware.
This file was GENERATED by Ronin Forge. Running it is a separate, manual step;
generating it does NOT train a model.
"""
import subprocess, sys

CMD = [
    sys.executable, "-m", "mlx_lm.lora",
    "--model", "{c.base_model}",
    "--train", "--data", ".",
    "--fine-tune-type", "{'lora' if c.method in ('lora', 'qlora') else 'full'}",
    "--iters", "{c.iters}",
    "--batch-size", "{c.batch_size}",
    "--num-layers", "{c.lora_layers}",
    "--learning-rate", "{c.learning_rate}",
    "--max-seq-length", "{c.max_seq_length}",
    "--seed", "{c.seed}",
    "--grad-checkpoint",
    "--adapter-path", "adapters",
]

if __name__ == "__main__":
    print("Running:", " ".join(CMD))
    raise SystemExit(subprocess.call(CMD))
'''


def _readme(c: BundleConfig, n: int) -> str:
    est = c.estimate()
    return f"""# Training bundle — {c.industry} ({c.method})

**Status: GENERATED. Nothing has been trained.** This directory is a complete,
runnable bundle. Training is a separate manual step on hardware you provide.

- base model: `{c.base_model}`
- training rows (eligible only): {n}
- seed: {c.seed} (reproducible)

## Estimates (heuristic, not guarantees)

- min VRAM (QLoRA 1.5B): ~{est['min_vram_gb_qlora_1_5b']} GB
- recommended RAM: ~{est['recommended_ram_gb']} GB
- approx wall-clock: ~{est['approx_wallclock_minutes']} min
- approx cloud GPU cost: ~${est['approx_cost_usd_cloud_gpu']}

## Run

```bash
pip install -r requirements.txt   # Apple Silicon: mlx-lm
python train.py                   # writes adapters/
```

## Evaluate before trusting

```bash
python -m ronin_training.eval_runner \\
    --model {c.base_model} --adapter adapters \\
    --evals ../evals/ronin_protocol_eval.jsonl --out eval.md
```

Compare against the base model and the current best adapter. Only promote via
the adapter registry after evals pass and a human approves.

## Serve locally (after training)

```bash
ollama create ronin-tuned -f Modelfile   # if exported to GGUF
# or point Ronin at the MLX adapter:
export RONIN_ADAPTER=$(pwd)/adapters
ronin --provider local "read pyproject.toml and tell me the package name"
```
"""


def _model_card(c: BundleConfig) -> str:
    return f"""# Model card (template) — fill in after training

- base model: {c.base_model}
- method: {c.method}
- industry: {c.industry}
- intended use: behavior/format/tool-selection for the {c.industry} world
- NOT intended for: current-facts recall (use retrieval + tools)
- training data: see the dataset provenance report; training-eligible items only
- evaluation: (paste eval_runner results here — base vs adapter, same set)
- limitations: (fill in — a fine-tune does not guarantee accuracy)
- safety evaluations: (paste required safety-suite results)
"""


def _modelfile(c: BundleConfig) -> str:
    return f"""# Ollama Modelfile (for the GGUF export path)
FROM ./model.gguf
TEMPLATE \"\"\"<|im_start|>system
{{{{ .System }}}}<|im_end|>
<|im_start|>user
{{{{ .Prompt }}}}<|im_end|>
<|im_start|>assistant
\"\"\"
PARAMETER stop <|im_end|>
SYSTEM \"\"\"You are Ronin, running on a locally fine-tuned {c.industry} adapter.\"\"\"
"""
