"""The DPO trainer profile — a preference pass *onto the SFT adapter*, gently.

DPO here is a correction pass, not a fresh training: it starts from the SFT adapter, moves
a little, and keeps the SFT model as a frozen reference so the KL term can measure "how far
have we drifted". Three numbers encode that and are validated, not just documented:

* **init from the SFT adapter.** ``init_adapter`` must be set — DPO onto the base model is
  almost never what someone means (there is no behaviour to *prefer-tune* yet), so an empty
  ``init_adapter`` is refused. The reference model is that same SFT checkpoint, frozen.
* **lr an order of magnitude below SFT.** ``5e-7`` against SFT's ``1e-5`` (20×). DPO at the
  SFT learning rate blows past the reference and the model forgets how to format a tool
  call while learning to prefer one. The validator refuses an lr above :data:`MAX_DPO_LR`.
* **the loss, and its escape hatch.** ``sigmoid`` (standard DPO) by default; ``ipo`` is the
  documented fallback when sigmoid over-optimises (the reward margin grows while the model
  degrades). ``loss`` must be one of the two, so a typo cannot silently pick something else.

``beta`` (``0.1``) and ``epochs`` (``1``) are the user's numbers. No GPU here ran DPO to
tune any of them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import yaml  # type: ignore[import-untyped]  # training is outside the mypy gate; typed anyway

from ronin_training.adapter.config import REQUIRED_TARGETS

#: DPO must stay far below the SFT learning rate (1e-5). Above this, the preference pass
#: overpowers the reference and undoes the SFT format the model just learned.
MAX_DPO_LR: Final[float] = 1.0e-6

#: The two losses this pass supports: standard DPO, and the fallback when it over-optimises.
LOSSES: Final[tuple[str, ...]] = ("sigmoid", "ipo")


class DPOProfileError(ValueError):
    """A DPO profile that would forget the SFT model or train the wrong loss, refused."""


@dataclass(frozen=True, slots=True)
class DPOProfile:
    """One DPO run's shape. Defaults are the spec's values for the 7b pass."""

    name: str = "qwen7b-dpo"
    base_model: str = "Qwen/Qwen2.5-Coder-7B-Instruct"
    base_model_mlx: str = "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit"
    #: The SFT adapter this pass starts from AND references (frozen). Required.
    init_adapter: str = "training/adapters/sft"
    lane: str = "peft"
    rank: int = 32
    alpha: int = 64
    target_modules: tuple[str, ...] = REQUIRED_TARGETS
    max_seq_length: int = 16384
    learning_rate: float = 5.0e-7
    beta: float = 0.1
    loss: str = "sigmoid"
    epochs: int = 1
    packing: bool = False

    def validate(self) -> None:
        """Refuse the mistakes that make a DPO pass forget the SFT model."""
        if not self.init_adapter:
            raise DPOProfileError(
                "init_adapter is required: DPO starts from (and references) the SFT adapter. "
                "DPO onto the base model has no behaviour to prefer-tune."
            )
        if self.learning_rate > MAX_DPO_LR:
            raise DPOProfileError(
                f"lr {self.learning_rate:g} is too high for DPO (max {MAX_DPO_LR:g}); it must "
                "sit ~10-20x below the SFT lr or the preference pass overruns the reference"
            )
        if self.loss not in LOSSES:
            raise DPOProfileError(f"loss must be one of {list(LOSSES)}, got {self.loss!r}")
        if self.packing:
            raise DPOProfileError("packing must be False: it corrupts the per-turn boundaries")
        missing = tuple(t for t in REQUIRED_TARGETS if t not in self.target_modules)
        if missing:
            raise DPOProfileError(f"LoRA targets must cover {list(REQUIRED_TARGETS)}; missing {list(missing)}")
        if self.beta <= 0.0:
            raise DPOProfileError("beta must be > 0")
        if self.epochs < 1:
            raise DPOProfileError("epochs must be >= 1")

    @property
    def ref_adapter(self) -> str:
        """The frozen reference model: the SFT adapter this pass started from."""
        return self.init_adapter

    def run_command(self, config_path: str, data_dir: str, adapter_out: str) -> list[str]:
        """The command that runs this profile — the real DPO entrypoint, built not launched."""
        return [
            "python", "-m", "ronin_training.dpo.train",
            "--config", config_path,
            "--data", data_dir,
            "--out", adapter_out,
            "--lane", self.lane,
        ]

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> DPOProfile:
        known = set(cls.__dataclass_fields__)
        unknown = set(data) - known
        if unknown:
            raise DPOProfileError(f"unknown dpo profile keys: {sorted(unknown)}")
        kwargs = dict(data)
        if "target_modules" in kwargs:
            kwargs["target_modules"] = tuple(kwargs["target_modules"])
        profile = cls(**kwargs)
        profile.validate()
        return profile

    @classmethod
    def load(cls, path: str | Path) -> DPOProfile:
        """Read and validate a DPO profile yaml. The 'config loads' acceptance."""
        parsed = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        if not isinstance(parsed, dict):
            raise DPOProfileError(f"{path}: expected a mapping at the top level")
        return cls.from_mapping(parsed)


QWEN7B_DPO: Final[DPOProfile] = DPOProfile()


__all__ = [
    "LOSSES",
    "MAX_DPO_LR",
    "QWEN7B_DPO",
    "DPOProfile",
    "DPOProfileError",
]
