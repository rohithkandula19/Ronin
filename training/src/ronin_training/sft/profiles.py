"""The SFT trainer profiles — qlora on qwen2.5-coder-7b, plus a 1.5b debug profile.

Two lanes, one config shape:

* **7b (the real run):** qlora, rank 32 / alpha 64, LoRA on q/k/v/o + gate/up/down, seq
  16384, lr 1e-5 cosine, 2 epochs, on the trl/axolotl (peft) path.
* **1.5b (debug):** the same everything at a size that runs on a free T4 / a laptop MLX
  lane, so the pipeline can be exercised end to end quickly before the 7b run.

Two rules are load-bearing and validated, not just documented:

* **NO sequence packing.** Packing concatenates several examples into one sequence to fill
  the context; it also runs the loss across the join, which corrupts the assistant-only
  mask (:mod:`ronin_training.sft.masking`) — a token from example B gets a label from
  example A's turn boundaries. So ``packing`` must be ``False``, and :meth:`SFTProfile.validate`
  refuses ``True`` with the reason. This is the config half of the masking guarantee.
* **The LoRA targets must cover the seven the spec names.** A profile that drops
  ``gate_proj`` trains a different, weaker adapter than the one the numbers were chosen
  for; the targets are checked against :data:`ronin_training.adapter.config.REQUIRED_TARGETS`.

The rank/alpha/seq numbers are the user's; where the spec was a range ("2 epochs") the
value is concrete and validated. No GPU here ran SFT to tune any of them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import yaml  # type: ignore[import-untyped]  # training is outside the mypy gate; typed anyway

from ronin_training.adapter.config import REQUIRED_TARGETS


class SFTProfileError(ValueError):
    """A profile that would train the wrong adapter or corrupt the mask, refused."""


@dataclass(frozen=True, slots=True)
class SFTProfile:
    """One SFT run's shape. Defaults are the 7b real-run values."""

    name: str = "qwen7b-qlora"
    base_model: str = "Qwen/Qwen2.5-Coder-7B-Instruct"
    base_model_mlx: str = "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit"
    lane: str = "peft"  # "peft" (trl/axolotl, the 7b lane) or "mlx" (the 1.5b/laptop lane)
    quant: str = "qlora"  # 4-bit base + LoRA — the memory the 7b run needs on a T4
    rank: int = 32
    alpha: int = 64
    target_modules: tuple[str, ...] = REQUIRED_TARGETS
    max_seq_length: int = 16384
    learning_rate: float = 1.0e-5
    schedule: str = "cosine"
    epochs: int = 2
    packing: bool = False

    def validate(self) -> None:
        """Refuse the two mistakes that make an SFT run quietly worthless."""
        if self.packing:
            raise SFTProfileError(
                "packing must be False for agent SFT: concatenating examples runs the loss "
                "across the join and corrupts the assistant-only mask (see sft.masking)"
            )
        missing = tuple(t for t in REQUIRED_TARGETS if t not in self.target_modules)
        if missing:
            raise SFTProfileError(
                f"LoRA targets must cover {list(REQUIRED_TARGETS)}; missing {list(missing)}"
            )
        if self.rank < 1 or self.alpha < 1:
            raise SFTProfileError("rank and alpha must be >= 1")
        if self.max_seq_length < 1:
            raise SFTProfileError("max_seq_length must be >= 1")
        if self.epochs < 1:
            raise SFTProfileError("epochs must be >= 1")
        if self.lane not in ("peft", "mlx"):
            raise SFTProfileError(f"lane must be 'peft' or 'mlx', got {self.lane!r}")
        if self.quant not in ("qlora", "lora"):
            raise SFTProfileError(f"quant must be 'qlora' or 'lora', got {self.quant!r}")

    @property
    def scale(self) -> float:
        """PEFT's effective LoRA scale, ``alpha / rank``."""
        return self.alpha / self.rank

    def run_command(self, config_path: str, data_dir: str, adapter_out: str) -> list[str]:
        """The command that runs this profile — the real SFT entrypoint, built not launched.

        Both lanes go through ``python -m ronin_training.sft.train``: the entrypoint
        validates the data offline, then drives trl (peft/7b) or ``mlx_lm.lora`` (mlx/1.5b)
        for the lane. Returning argv rather than launching is what makes the recipe
        reviewable and testable with no trainer, no weights, and no GPU present.
        """
        return [
            "python", "-m", "ronin_training.sft.train",
            "--config", config_path,
            "--data", data_dir,
            "--out", adapter_out,
            "--lane", self.lane,
        ]

    def mlx_lora_argv(self, data_dir: str, adapter_out: str) -> list[str]:
        """The underlying ``mlx_lm.lora`` command the mlx lane execs. Pure; built, not run."""
        return [
            "python", "-m", "mlx_lm.lora", "--train",
            "--model", self.base_model_mlx,
            "--data", data_dir,
            "--adapter-path", adapter_out,
            "--num-layers", "-1",
            "--max-seq-length", str(self.max_seq_length),
            "--learning-rate", str(self.learning_rate),
            "--iters", "0",
        ]

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> SFTProfile:
        known = set(cls.__dataclass_fields__)
        unknown = set(data) - known
        if unknown:
            raise SFTProfileError(f"unknown sft profile keys: {sorted(unknown)}")
        kwargs = dict(data)
        if "target_modules" in kwargs:
            kwargs["target_modules"] = tuple(kwargs["target_modules"])
        profile = cls(**kwargs)
        profile.validate()
        return profile

    @classmethod
    def load(cls, path: str | Path) -> SFTProfile:
        """Read and validate a profile yaml. The 'config loads' acceptance."""
        parsed = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        if not isinstance(parsed, dict):
            raise SFTProfileError(f"{path}: expected a mapping at the top level")
        return cls.from_mapping(parsed)


#: The two built-in profiles, also written to yaml under training/config for the CLI.
QWEN7B_QLORA: Final[SFTProfile] = SFTProfile()
QWEN1P5B_DEBUG: Final[SFTProfile] = SFTProfile(
    name="qwen1.5b-debug",
    base_model="Qwen/Qwen2.5-Coder-1.5B-Instruct",
    base_model_mlx="mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit",
    lane="mlx",
    max_seq_length=4096,
)


__all__ = [
    "QWEN1P5B_DEBUG",
    "QWEN7B_QLORA",
    "SFTProfile",
    "SFTProfileError",
]
