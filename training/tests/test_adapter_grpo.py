"""The GRPO pass: parsed, validated, projected to trl — and refused where mlx cannot run it.

GRPO is the RF.3 pass and the only one whose reward is a verifier rather than a label or a
preference pair. These tests pin the config contract (a group needs more than one member;
the reward must be a known verifier; the pass is peft/trl-only) and prove the shipped
`adapter_grpo.yaml` loads and validates end to end.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from ronin_training.adapter.config import (
    GRPO_CONFIG_PATH,
    ConfigError,
    GRPOSpec,
    TrainPass,
    load_config,
    parse_config,
    validate,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _grpo_data(*, grpo: dict[str, Any] | None = None, **over: Any) -> dict[str, Any]:
    """A minimal, valid GRPO config as parsed YAML would present it.

    ``grpo=`` overrides individual keys of the [grpo] block; other keywords override
    top-level keys.
    """
    block: dict[str, Any] = {
        "beta": 0.04,
        "num_generations": 8,
        "temperature": 0.9,
        "max_completion_length": 256,
        "reward": "verifiable_tool_call",
    }
    block.update(grpo or {})
    data: dict[str, Any] = {
        "pass": "grpo",
        "resume_adapter": "training/adapters/x/sft",
        "train": {"epochs": 1},
        "grpo": block,
    }
    data.update(over)
    return data


# --------------------------------------------------------------------------- #
# parse
# --------------------------------------------------------------------------- #


def test_a_grpo_config_parses_into_a_grpo_spec() -> None:
    cfg = parse_config(_grpo_data())
    assert cfg.pass_ is TrainPass.GRPO
    assert cfg.grpo == GRPOSpec(
        beta=0.04, num_generations=8, temperature=0.9, max_completion_length=256
    )
    assert cfg.dpo is None


def test_an_unknown_grpo_key_is_fatal() -> None:
    with pytest.raises(ConfigError, match="unknown key"):
        parse_config(_grpo_data(grpo={"kl": 0.1}))


# --------------------------------------------------------------------------- #
# validate
# --------------------------------------------------------------------------- #


def test_a_well_formed_grpo_config_validates() -> None:
    assert validate(parse_config(_grpo_data())).ok


def test_a_group_of_one_is_rejected() -> None:
    result = validate(parse_config(_grpo_data(grpo={"num_generations": 1})))
    assert not result.ok
    assert any("num_generations" in e for e in result.errors)


def test_a_zero_temperature_group_has_no_spread_and_is_rejected() -> None:
    result = validate(parse_config(_grpo_data(grpo={"temperature": 0.0})))
    assert any("temperature" in e for e in result.errors)


def test_an_unknown_reward_is_rejected_by_name() -> None:
    result = validate(parse_config(_grpo_data(grpo={"reward": "a_learned_model"})))
    assert any("reward" in e and "verifiable_tool_call" in e for e in result.errors)


def test_a_grpo_pass_may_not_carry_a_dpo_block() -> None:
    result = validate(parse_config(_grpo_data(dpo={"beta": 0.1})))
    assert any("must not carry a [dpo] block" in e for e in result.errors)


def test_a_grpo_pass_missing_its_block_is_rejected() -> None:
    data = _grpo_data()
    del data["grpo"]
    result = validate(parse_config(data))
    assert any("requires a [grpo] block" in e for e in result.errors)


def test_grpo_from_the_base_model_only_warns() -> None:
    result = validate(parse_config(_grpo_data(resume_adapter="")))
    assert result.ok  # a warning, not an error — GRPO-from-base is a real experiment
    assert any("no resume_adapter" in w for w in result.warnings)


def test_the_sft_pass_may_not_carry_a_grpo_block() -> None:
    data = {
        "pass": "sft",
        "train": {"epochs": 2},
        "grpo": {"num_generations": 8},
    }
    result = validate(parse_config(data))
    assert any("must not carry a [grpo] block" in e for e in result.errors)


# --------------------------------------------------------------------------- #
# projection — trl only, no mlx lane
# --------------------------------------------------------------------------- #


def test_to_peft_kwargs_carries_the_group_size_no_other_pass_has() -> None:
    cfg = parse_config(_grpo_data())
    kwargs = cfg.to_peft_kwargs()
    assert kwargs["grpo"]["num_generations"] == 8
    assert kwargs["grpo"]["beta"] == 0.04
    # the shared optimiser block is folded in, exactly as DPO folds it
    assert kwargs["grpo"]["output_dir"] == cfg.adapter_out


def test_grpo_has_no_mlx_projection_and_says_so() -> None:
    cfg = parse_config(_grpo_data())
    with pytest.raises(ConfigError, match="no mlx-lm projection"):
        cfg.to_mlx_config(train_rows=100)


# --------------------------------------------------------------------------- #
# the shipped config, end to end
# --------------------------------------------------------------------------- #


def test_the_shipped_grpo_config_loads_and_validates() -> None:
    cfg = load_config(_REPO_ROOT / GRPO_CONFIG_PATH)
    assert cfg.pass_ is TrainPass.GRPO
    result = validate(cfg, train_rows=400)
    assert result.ok, result.render()
    # the one warning that must be present: there is no mlx lane for this pass
    assert any("no mlx-lm lane" in w for w in result.warnings)
