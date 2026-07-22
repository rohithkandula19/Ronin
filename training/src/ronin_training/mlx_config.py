"""Build the on-device MLX training command from a config — deterministically.

This module does NOT train. It turns a `MLXTrainConfig` into the exact `mlx_lm.lora`
argument list you would run on an Apple Silicon Mac, plus a couple of honest guards:

- 8 GB is tight. QLoRA on a 4-bit 1.5B model is the default because it fits; the full
  fine-tune lane (`fine_tune_type="full"`) and the 1.5B non-quantized lane are real
  options the spec asks for, but they need more RAM and will OOM on an 8 GB M2. The
  config exposes them; it does not pretend they're free.
- Nothing here fabricates a result. Running the command, reading its loss curve, and
  evaluating the adapter (see `eval_runner`) are separate, observable steps.

Reference: `mlx_lm.lora` CLI (mlx-lm >= 0.20). QLoRA = LoRA over a pre-quantized
(4-bit) base model; there is no separate `--qlora` flag — you point `--model` at a
quantized repo and keep `--fine-tune-type lora`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Smallest viable Ronin targets (spec: use the smallest model that can learn the
# protocol). QLoRA-friendly 4-bit variants fit an 8 GB machine; the 0.5B is the
# fallback if the 1.5B OOMs during training.
DEFAULT_MODEL = "mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit"
FALLBACK_MODEL = "mlx-community/Qwen2.5-Coder-0.5B-Instruct-4bit"

_FINE_TUNE_TYPES = ("lora", "dora", "full")


@dataclass
class MLXTrainConfig:
    data_dir: str = "training/data/generated"
    model: str = DEFAULT_MODEL
    adapter_path: str = "training/adapters/ronin"
    fine_tune_type: str = "lora"      # "lora" (incl. QLoRA via a 4-bit model), "dora", "full"
    iters: int = 300                  # a smoke run; raise once the loss curve looks sane
    batch_size: int = 1               # 8 GB reality; bump only with headroom
    num_layers: int = 8               # LoRA layers from the top; ignored for full
    learning_rate: float = 1e-5
    max_seq_length: int = 2048
    steps_per_eval: int = 50
    save_every: int = 100
    seed: int = 0                     # fixed → reproducible run
    grad_checkpoint: bool = True      # trade compute for memory on small RAM

    def __post_init__(self) -> None:
        if self.fine_tune_type not in _FINE_TUNE_TYPES:
            raise ValueError(
                f"fine_tune_type must be one of {_FINE_TUNE_TYPES}, got {self.fine_tune_type!r}"
            )

    @property
    def is_quantized_base(self) -> bool:
        m = self.model.lower()
        return "4bit" in m or "-8bit" in m or "int4" in m

    @property
    def is_qlora(self) -> bool:
        return self.fine_tune_type == "lora" and self.is_quantized_base

    def memory_warnings(self) -> list[str]:
        """Honest, static heuristics — not a guarantee the run fits."""
        warn: list[str] = []
        if self.fine_tune_type == "full":
            warn.append(
                "full fine-tune updates all weights; on an 8 GB M2 this will very "
                "likely OOM even for a 0.5B model — use a machine with more RAM."
            )
        if self.fine_tune_type == "lora" and not self.is_quantized_base:
            warn.append(
                "LoRA over a non-quantized base is heavier than QLoRA; point --model "
                "at a 4-bit repo to fit 8 GB, or expect to reduce batch/seq length."
            )
        if self.batch_size > 1:
            warn.append("batch_size > 1 raises peak memory; 8 GB usually needs batch_size=1.")
        return warn

    def command(self) -> list[str]:
        """The exact `mlx_lm.lora` argv. Run it yourself; this only builds it."""
        argv = [
            "python", "-m", "mlx_lm.lora",
            "--model", self.model,
            "--train",
            "--data", self.data_dir,
            "--fine-tune-type", self.fine_tune_type,
            "--iters", str(self.iters),
            "--batch-size", str(self.batch_size),
            "--learning-rate", str(self.learning_rate),
            "--max-seq-length", str(self.max_seq_length),
            "--steps-per-eval", str(self.steps_per_eval),
            "--save-every", str(self.save_every),
            "--seed", str(self.seed),
            "--adapter-path", self.adapter_path,
        ]
        if self.fine_tune_type != "full":
            argv += ["--num-layers", str(self.num_layers)]
        if self.grad_checkpoint:
            argv += ["--grad-checkpoint"]
        return argv

    def command_str(self) -> str:
        return " ".join(self.command())
