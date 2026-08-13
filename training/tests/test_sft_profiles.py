"""SFT profiles load and validate; the no-packing and target-coverage rules bite."""

from __future__ import annotations

from pathlib import Path

import pytest
from ronin_training.adapter.config import REQUIRED_TARGETS
from ronin_training.sft.profiles import (
    QWEN1P5B_DEBUG,
    QWEN7B_QLORA,
    SFTProfile,
    SFTProfileError,
)

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


def test_the_7b_profile_has_the_spec_values() -> None:
    p = QWEN7B_QLORA
    p.validate()
    assert p.base_model == "Qwen/Qwen2.5-Coder-7B-Instruct"
    assert p.quant == "qlora"
    assert (p.rank, p.alpha) == (32, 64)
    assert p.scale == 2.0
    assert set(p.target_modules) >= set(REQUIRED_TARGETS)
    assert p.max_seq_length == 16384
    assert p.learning_rate == pytest.approx(1.0e-5)
    assert p.schedule == "cosine"
    assert p.epochs == 2
    assert p.packing is False
    assert p.lane == "peft"


def test_the_1p5b_debug_profile_is_the_fast_mlx_lane() -> None:
    p = QWEN1P5B_DEBUG
    p.validate()
    assert "1.5B" in p.base_model
    assert p.lane == "mlx"
    assert p.max_seq_length == 4096


def test_packing_is_refused_because_it_corrupts_the_mask() -> None:
    from dataclasses import replace

    with pytest.raises(SFTProfileError, match="packing must be False"):
        replace(QWEN7B_QLORA, packing=True).validate()


def test_dropping_a_lora_target_is_refused() -> None:
    from dataclasses import replace

    with pytest.raises(SFTProfileError, match="targets must cover"):
        replace(QWEN7B_QLORA, target_modules=("q_proj", "v_proj")).validate()


def test_both_committed_yaml_profiles_load() -> None:
    seven = SFTProfile.load(_CONFIG_DIR / "sft_qwen7b.yaml")
    debug = SFTProfile.load(_CONFIG_DIR / "sft_qwen1p5b_debug.yaml")
    assert (seven.rank, seven.alpha, seven.max_seq_length) == (32, 64, 16384)
    assert seven.packing is False and debug.packing is False
    assert debug.lane == "mlx" and seven.lane == "peft"


def test_an_unknown_key_is_refused() -> None:
    with pytest.raises(SFTProfileError, match="unknown sft profile keys"):
        SFTProfile.from_mapping({"rank": 32, "nonsense": True})


def test_the_trainer_argv_differs_by_lane_and_never_packs() -> None:
    peft = QWEN7B_QLORA.trainer_argv("data", "out")
    assert "--no-packing" in peft
    assert "ronin_training.sft.train_peft" in peft
    assert "32" in peft and "64" in peft  # rank / alpha reach the command

    mlx = QWEN1P5B_DEBUG.trainer_argv("data", "out")
    assert "mlx_lm.lora" in mlx
    assert "--train" in mlx
