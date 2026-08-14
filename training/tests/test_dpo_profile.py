"""The DPO profile loads and validates; the gentle-pass rules bite."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from ronin_training.dpo.profile import MAX_DPO_LR, QWEN7B_DPO, DPOProfile, DPOProfileError

_CONFIG = Path(__file__).resolve().parents[1] / "config" / "dpo_qwen7b.yaml"


def test_the_7b_dpo_profile_has_the_spec_values() -> None:
    p = QWEN7B_DPO
    p.validate()
    assert p.learning_rate == pytest.approx(5.0e-7)
    assert p.learning_rate <= MAX_DPO_LR
    assert p.beta == pytest.approx(0.1)
    assert p.loss == "sigmoid"
    assert p.epochs == 1
    assert p.init_adapter == "training/adapters/sft"
    assert p.ref_adapter == p.init_adapter  # the frozen reference is the SFT adapter
    assert p.packing is False


def test_dpo_onto_the_base_model_is_refused() -> None:
    with pytest.raises(DPOProfileError, match="init_adapter is required"):
        replace(QWEN7B_DPO, init_adapter="").validate()


def test_an_sft_scale_learning_rate_is_refused() -> None:
    with pytest.raises(DPOProfileError, match="too high for DPO"):
        replace(QWEN7B_DPO, learning_rate=1.0e-5).validate()


def test_ipo_is_the_allowed_fallback_but_a_typo_is_not() -> None:
    replace(QWEN7B_DPO, loss="ipo").validate()  # the documented fallback
    with pytest.raises(DPOProfileError, match="loss must be one of"):
        replace(QWEN7B_DPO, loss="dpo").validate()


def test_the_committed_yaml_loads() -> None:
    p = DPOProfile.load(_CONFIG)
    assert (p.learning_rate, p.beta, p.loss, p.epochs) == (5.0e-7, 0.1, "sigmoid", 1)
    assert p.init_adapter == "training/adapters/sft"


def test_run_command_points_at_the_real_entrypoint() -> None:
    cmd = QWEN7B_DPO.run_command(str(_CONFIG), "data", "out")
    assert cmd[:3] == ["python", "-m", "ronin_training.dpo.train"]
    assert "--config" in cmd and "--lane" in cmd


def test_an_unknown_key_is_refused() -> None:
    with pytest.raises(DPOProfileError, match="unknown dpo profile keys"):
        DPOProfile.from_mapping({"beta": 0.1, "nonsense": 1})
