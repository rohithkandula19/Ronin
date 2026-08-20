"""The GRPO config loads and validates; the curriculum filter and DAPO helpers are correct."""

from __future__ import annotations

from pathlib import Path

import pytest
from ronin_training.rl.config import (
    CurriculumBand,
    GRPOConfig,
    RLConfigError,
    curriculum_keep,
)
from ronin_training.rl.loss import assistant_token_mask, broadcast_reward, dapo_normalize

_CONFIG = Path(__file__).resolve().parents[1] / "config" / "rl_grpo.yaml"


# --------------------------------------------------------------------------- #
# The config loads — the acceptance
# --------------------------------------------------------------------------- #


def test_the_committed_grpo_config_loads_with_the_spec_values() -> None:
    config = GRPOConfig.load(_CONFIG)
    assert config.group_size == 16
    assert config.prompts_per_batch == 32
    assert config.learning_rate == pytest.approx(1.0e-6)
    assert config.kl_coef == pytest.approx(0.001)
    assert (config.clip_low, config.clip_high) == (0.2, 0.28)
    assert config.max_turns == 24
    assert config.max_seq_length == 32768
    assert (config.min_steps, config.max_steps) == (300, 800)
    assert config.rollout_backend == "vllm"
    assert (config.async_rollouts_min, config.async_rollouts_max) == (64, 256)
    assert (config.curriculum.low, config.curriculum.high) == (0.15, 0.85)
    assert config.curriculum.refilter_every_steps == 50


def test_an_unknown_key_is_refused() -> None:
    with pytest.raises(RLConfigError, match="unknown rl_grpo keys"):
        GRPOConfig.from_mapping({"group_size": 16, "nonsense": 1})


def test_validation_rejects_contradictions() -> None:
    with pytest.raises(RLConfigError, match="group_size"):
        GRPOConfig(group_size=1).validate()
    with pytest.raises(RLConfigError, match="clip"):
        GRPOConfig(clip_low=0.5, clip_high=0.2).validate()
    with pytest.raises(RLConfigError, match="min_steps"):
        GRPOConfig(min_steps=900, max_steps=800).validate()
    with pytest.raises(RLConfigError, match="vllm"):
        GRPOConfig(rollout_backend="tgi").validate()


def test_a_bad_curriculum_band_is_refused() -> None:
    with pytest.raises(RLConfigError, match="band"):
        CurriculumBand(low=0.9, high=0.1)


# --------------------------------------------------------------------------- #
# Curriculum filter — keep the tasks that still teach
# --------------------------------------------------------------------------- #


def test_curriculum_keeps_only_the_learnable_band() -> None:
    rates = {
        "too-easy": 0.95,
        "goldilocks": 0.5,
        "edge-low": 0.15,
        "edge-high": 0.85,
        "too-hard": 0.0,
    }
    kept = curriculum_keep(rates)
    assert kept == ("edge-high", "edge-low", "goldilocks")  # sorted, band-inclusive


def test_curriculum_drops_unmeasured_tasks_rather_than_guessing() -> None:
    kept = curriculum_keep({"measured": 0.5})
    assert kept == ("measured",)


# --------------------------------------------------------------------------- #
# Multi-turn correctness — the mask, the reward broadcast, the DAPO normalisation
# --------------------------------------------------------------------------- #


def test_the_assistant_mask_is_one_only_on_the_spans() -> None:
    mask = assistant_token_mask([(2, 4), (6, 7)], 8)
    assert mask == [0, 0, 1, 1, 0, 0, 1, 0]


def test_a_span_past_the_end_is_clamped_and_a_bad_span_raises() -> None:
    assert assistant_token_mask([(0, 99)], 3) == [1, 1, 1]
    with pytest.raises(ValueError, match="bad assistant span"):
        assistant_token_mask([(3, 1)], 5)


def test_the_reward_is_broadcast_uniformly_over_assistant_tokens_only() -> None:
    mask = [0, 1, 1, 0]
    assert broadcast_reward(0.7, mask) == [0.0, 0.7, 0.7, 0.0]


def test_dapo_normalises_by_total_tokens_so_length_cannot_be_gamed() -> None:
    # Two sequences, one long and one short, each with a per-token loss of 1.0 on the
    # assistant tokens. DAPO divides the summed loss by the TOTAL assistant tokens, so the
    # result is the mean per-token loss (1.0) regardless of how the tokens split across
    # sequences — a long trajectory is not down-weighted the way a per-sequence mean would.
    losses = [[1.0, 1.0, 1.0, 1.0], [1.0]]
    masks = [[1, 1, 1, 1], [1]]
    assert dapo_normalize(losses, masks) == pytest.approx(1.0)

    # Masked (non-assistant) tokens do not count in the denominator or the numerator.
    assert dapo_normalize([[9.0, 1.0]], [[0, 1]]) == pytest.approx(1.0)


def test_dapo_on_an_empty_batch_is_zero_not_a_zero_division() -> None:
    assert dapo_normalize([[1.0]], [[0]]) == 0.0
