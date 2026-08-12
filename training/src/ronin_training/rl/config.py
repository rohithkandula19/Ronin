"""The agentic-GRPO config, and the curriculum filter — the knobs a verl/SkyRL run reads.

This is a *different* config from :class:`ronin_training.adapter.config.GRPOSpec`. That one
drives ``trl.GRPOTrainer`` over single completions for the dialect-validity reward (short
sequences, ``num_generations`` samples, an mlx-less lane). This one drives a **multi-turn
agentic** RL loop: each "prompt" is a task, each sample is a whole 24-turn rollout in a
container, the reward is ``verify.sh`` (:mod:`ronin_training.rl.reward`), and the policy is
served by vLLM with weights synced every step. They share a name and nothing else, so they
are deliberately separate files rather than one overloaded config.

Every number is the value the spec named; where the spec was silent the default is a
documented, conventional one, never a figure dressed up as tuned — no GPU here has run
this loop to sweep anything. :meth:`GRPOConfig.load` reads the committed
``training/config/rl_grpo.yaml`` and validates it, which is the whole of the "the config
loads" acceptance.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]  # training is outside the mypy gate; typed anyway


class RLConfigError(ValueError):
    """A config that would train the wrong thing, refused with the reason."""


@dataclass(frozen=True, slots=True)
class CurriculumBand:
    """Keep only tasks whose rollout pass rate sits in ``[low, high]``.

    A task the policy already passes every time (rate → 1) and one it never passes (rate →
    0) both yield zero advantage under GRPO — every rollout in the group scores the same,
    so the group's gradient is nothing. The band keeps the tasks that still teach, and it
    is re-evaluated every :attr:`refilter_every_steps` because which tasks those are moves
    as the policy improves.
    """

    low: float = 0.15
    high: float = 0.85
    refilter_every_steps: int = 50

    def __post_init__(self) -> None:
        if not 0.0 <= self.low < self.high <= 1.0:
            raise RLConfigError(f"curriculum band must be 0<=low<high<=1, got [{self.low}, {self.high}]")
        if self.refilter_every_steps < 1:
            raise RLConfigError("refilter_every_steps must be >= 1")


@dataclass(frozen=True, slots=True)
class GRPOConfig:
    """The agentic-GRPO run. Defaults are the spec's stated values."""

    group_size: int = 16
    prompts_per_batch: int = 32
    learning_rate: float = 1.0e-6
    kl_coef: float = 0.001
    clip_low: float = 0.2
    clip_high: float = 0.28
    max_turns: int = 24
    max_seq_length: int = 32768
    min_steps: int = 300
    max_steps: int = 800
    rollout_backend: str = "vllm"
    async_rollouts_min: int = 64
    async_rollouts_max: int = 256
    pool_dir: str = "pool"
    seed: int = 0
    curriculum: CurriculumBand = field(default_factory=CurriculumBand)

    def validate(self) -> None:
        """Refuse a config whose numbers contradict the loop they describe."""
        if self.group_size < 2:
            raise RLConfigError("group_size must be >= 2: GRPO needs a group to form advantages")
        if self.prompts_per_batch < 1:
            raise RLConfigError("prompts_per_batch must be >= 1")
        if not 0.0 < self.clip_low <= self.clip_high:
            raise RLConfigError(f"need 0<clip_low<=clip_high, got {self.clip_low}/{self.clip_high}")
        if self.max_turns < 1:
            raise RLConfigError("max_turns must be >= 1")
        if not 1 <= self.min_steps <= self.max_steps:
            raise RLConfigError(f"need 1<=min_steps<=max_steps, got {self.min_steps}/{self.max_steps}")
        if not 1 <= self.async_rollouts_min <= self.async_rollouts_max:
            raise RLConfigError("need 1<=async_rollouts_min<=async_rollouts_max")
        if self.kl_coef < 0.0:
            raise RLConfigError("kl_coef must be >= 0")
        if self.rollout_backend != "vllm":
            raise RLConfigError(f"only the vllm rollout backend is supported, got {self.rollout_backend!r}")

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> GRPOConfig:
        """Build from a plain mapping (the parsed yaml), validate, and return."""
        known = {f for f in cls.__dataclass_fields__ if f != "curriculum"}
        unknown = set(data) - known - {"curriculum"}
        if unknown:
            raise RLConfigError(f"unknown rl_grpo keys: {sorted(unknown)}")
        band_data = data.get("curriculum", {})
        band = CurriculumBand(
            low=float(band_data.get("low", 0.15)),
            high=float(band_data.get("high", 0.85)),
            refilter_every_steps=int(band_data.get("refilter_every_steps", 50)),
        )
        kwargs = {key: data[key] for key in known if key in data}
        config = cls(curriculum=band, **kwargs)
        config.validate()
        return config

    @classmethod
    def load(cls, path: str | Path) -> GRPOConfig:
        """Read and validate the yaml config. The 'config loads' acceptance."""
        text = Path(path).read_text(encoding="utf-8")
        parsed = yaml.safe_load(text)
        if not isinstance(parsed, dict):
            raise RLConfigError(f"{path}: expected a mapping at the top level")
        return cls.from_mapping(parsed)


def curriculum_keep(
    pass_rates: Mapping[str, float], band: CurriculumBand = CurriculumBand()
) -> tuple[str, ...]:
    """The task ids to keep this round: those with a rollout pass rate inside the band.

    Sorted for determinism. A task with no measured rate is dropped — an unmeasured task
    cannot be shown to still teach, and guessing keeps it in the training set on faith.
    """
    kept = [task for task, rate in pass_rates.items() if band.low <= rate <= band.high]
    return tuple(sorted(kept))


__all__ = [
    "CurriculumBand",
    "GRPOConfig",
    "RLConfigError",
    "curriculum_keep",
]
