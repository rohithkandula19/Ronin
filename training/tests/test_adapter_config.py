"""The shipped adapter configs, and the validator that guards them.

The point of the first test in this file is bluntly defensive: it pins every
hyperparameter the user specified. If someone edits `adapter_sft.yaml` and changes the
learning rate or the LoRA rank, this fails by name. A config file nobody asserts on is
a config file that drifts.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from ronin_training.adapter.config import (
    BASE_MODEL,
    BASE_MODEL_LICENCE,
    DIALECT_CLOSE_TAG,
    DIALECT_OPEN_TAG,
    DPO_CONFIG_PATH,
    LONG_SEQ_LENGTH,
    MLX_ARGV_FLAGS,
    MLX_CONFIG_KEYS,
    REQUIRED_TARGETS,
    SEQ_LENGTH,
    SFT_CONFIG_PATH,
    AdapterConfig,
    ConfigError,
    DPOSpec,
    HoldoutSpec,
    LoRASpec,
    TrainPass,
    TrainSpec,
    dump_mlx_config,
    load_config,
    parse_config,
    validate,
    with_long_context,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SFT_PATH = REPO_ROOT / SFT_CONFIG_PATH
DPO_PATH = REPO_ROOT / DPO_CONFIG_PATH


# --------------------------------------------------------------------- shipped


def test_both_shipped_configs_exist_and_load() -> None:
    assert SFT_PATH.is_file(), SFT_PATH
    assert DPO_PATH.is_file(), DPO_PATH
    assert load_config(SFT_PATH).pass_ is TrainPass.SFT
    assert load_config(DPO_PATH).pass_ is TrainPass.DPO


@pytest.mark.parametrize("path", [SFT_PATH, DPO_PATH], ids=["sft", "dpo"])
def test_both_shipped_configs_validate_clean(path: Path) -> None:
    result = validate(load_config(path), train_rows=1000)
    assert result.ok, result.render()


def test_the_sft_config_carries_the_specified_hyperparameters_exactly() -> None:
    """Fails loudly if anyone edits lr, rank, alpha, targets, epochs or seq length."""
    cfg = load_config(SFT_PATH)
    assert cfg.base_model == BASE_MODEL
    assert cfg.base_model_licence == BASE_MODEL_LICENCE == "apache-2.0"
    assert cfg.lora.rank == 16
    assert cfg.lora.alpha == 32
    assert cfg.lora.target_modules == REQUIRED_TARGETS
    assert cfg.lora.target_modules == (
        "q_proj",
        "k_proj",
        "v_proj",
        "o_proj",
        "gate_proj",
        "up_proj",
        "down_proj",
    )
    assert cfg.train.learning_rate == 1e-5
    assert cfg.train.schedule == "cosine"
    assert cfg.train.epochs == 3
    assert cfg.train.max_seq_length == SEQ_LENGTH == 4096
    assert cfg.train.grad_checkpoint is True
    assert cfg.holdout.by == "task"
    assert cfg.holdout.fraction == pytest.approx(0.10)
    assert cfg.dpo is None
    assert (cfg.dialect_open_tag, cfg.dialect_close_tag) == (
        DIALECT_OPEN_TAG,
        DIALECT_CLOSE_TAG,
    )


def test_the_dpo_config_carries_beta_and_exactly_one_epoch() -> None:
    cfg = load_config(DPO_PATH)
    assert cfg.dpo is not None
    assert cfg.dpo.beta == 0.1
    assert cfg.train.epochs == 1
    assert cfg.resume_adapter, "DPO must start from the SFT checkpoint"
    assert cfg.lora.rank == 16 and cfg.lora.alpha == 32
    assert cfg.holdout.by == "task"


def test_the_dialect_tags_match_ronins_format_shim() -> None:
    """Drift guard across package boundaries.

    ``ronin_training`` cannot depend on ``ronin``, so the tags are duplicated. If the
    shim renames them, an adapter trained on the old tag would emit calls the runtime
    never parses — a silent total failure of tool-syntax validity. This test is the
    only thing that connects the two copies.
    """
    from ronin.providers.shim import CLOSE_TAG, OPEN_TAG

    assert DIALECT_OPEN_TAG == OPEN_TAG
    assert DIALECT_CLOSE_TAG == CLOSE_TAG


def test_the_dialect_is_not_the_v1_bare_tool_call_tag() -> None:
    """The v1 corpus (packages/dialect) uses ``<tool_call>``; this adapter must not."""
    assert DIALECT_OPEN_TAG != "<tool_call>"


# ------------------------------------------------------------------- projection


def test_the_mlx_projection_converts_alpha_over_rank_into_a_scale() -> None:
    """PEFT scales by alpha/rank; mlx-lm takes the scale directly.

    Getting this wrong halves or doubles the update and produces a different adapter
    from "the same" config, which is why it is arithmetic in one place with a test.
    """
    lora = LoRASpec(rank=16, alpha=32)
    assert lora.mlx_scale == 2.0
    assert LoRASpec(rank=8, alpha=32).mlx_scale == 4.0


def test_the_mlx_projection_qualifies_target_module_names() -> None:
    keys = LoRASpec().mlx_keys()
    assert "self_attn.q_proj" in keys
    assert "mlp.gate_proj" in keys
    assert len(keys) == len(REQUIRED_TARGETS)


def test_the_mlx_projection_only_emits_keys_mlx_lm_reads() -> None:
    for path in (SFT_PATH, DPO_PATH):
        projected = load_config(path).to_mlx_config(train_rows=500)
        assert set(projected) <= MLX_CONFIG_KEYS, set(projected) - MLX_CONFIG_KEYS


def test_the_mlx_argv_points_at_a_config_file_rather_than_flags() -> None:
    """rank/scale/keys/schedule have no CLI flags; argv alone would train r=8."""
    argv = load_config(SFT_PATH).to_mlx_argv(config_path="mlx_sft.yaml")
    assert argv[:4] == ["python", "-m", "mlx_lm.lora", "-c"]
    assert argv[-1] == "mlx_sft.yaml"
    tail = argv[argv.index("mlx_lm.lora") + 1 :]
    assert {token for token in tail if token.startswith("-")} <= MLX_ARGV_FLAGS


def test_the_mlx_projection_needs_a_row_count_rather_than_guessing_one() -> None:
    cfg = load_config(SFT_PATH)
    with pytest.raises(ConfigError, match="iteration count"):
        cfg.to_mlx_config()


def test_epochs_become_iterations_from_a_real_row_count() -> None:
    cfg = load_config(SFT_PATH)  # 3 epochs, batch 1 x grad_accum 4
    assert cfg.train.effective_batch == 4
    assert cfg.iters_for(400) == 300
    with pytest.raises(ConfigError, match="real number of training rows"):
        cfg.iters_for(0)


def test_the_peft_projection_carries_the_same_numbers() -> None:
    kwargs = load_config(SFT_PATH).to_peft_kwargs()
    assert kwargs["lora"]["r"] == 16
    assert kwargs["lora"]["lora_alpha"] == 32
    assert kwargs["train"]["learning_rate"] == 1e-5
    assert kwargs["train"]["lr_scheduler_type"] == "cosine"
    assert kwargs["train"]["num_train_epochs"] == 3
    assert kwargs["train"]["max_length"] == 4096
    assert "dpo" not in kwargs

    dpo = load_config(DPO_PATH).to_peft_kwargs()
    assert dpo["dpo"]["beta"] == 0.1
    assert dpo["dpo"]["num_train_epochs"] == 1


def test_dump_mlx_config_writes_a_readable_file(tmp_path: Path) -> None:
    out = dump_mlx_config(load_config(SFT_PATH), tmp_path / "mlx.yaml", train_rows=500)
    body = out.read_bytes()
    assert b"lora_parameters" in body
    assert b"\r\n" not in body, "CRLF would break the byte-identical reproduction claim"


# ------------------------------------------------------------------- validation


def _sft(**overrides: object) -> AdapterConfig:
    base = {
        "pass": "sft",
        "lora": {},
        "train": {},
        "holdout": {},
    }
    base.update(overrides)
    return parse_config(base)


def test_an_unknown_key_is_fatal_rather_than_silently_ignored() -> None:
    """`lr: 2e-4` instead of `learning_rate` must not train at 1e-5 in silence."""
    with pytest.raises(ConfigError, match="unknown key"):
        parse_config({"pass": "sft", "train": {"lr": 2e-4}})
    with pytest.raises(ConfigError, match="unknown key"):
        parse_config({"pass": "sft", "learning_rate": 2e-4})


def test_a_missing_or_bogus_pass_is_rejected() -> None:
    with pytest.raises(ConfigError, match="`pass` must be one of"):
        parse_config({})
    with pytest.raises(ConfigError, match="`pass` must be one of"):
        parse_config({"pass": "pretrain"})


def test_holdout_by_example_is_rejected_by_name() -> None:
    result = validate(_sft(holdout={"by": "example"}))
    assert not result.ok
    joined = " ".join(result.errors)
    assert "holdout.by must be 'task'" in joined
    assert "memorisation" in joined


def test_dropping_an_mlp_target_is_an_error() -> None:
    result = validate(
        _sft(lora={"target_modules": ["q_proj", "k_proj", "v_proj", "o_proj"]})
    )
    assert not result.ok
    assert any("gate_proj" in error for error in result.errors)


def test_eight_k_sequence_length_requires_gradient_checkpointing() -> None:
    """The long-context lane is a documented toggle, not a silent default."""
    broken = validate(
        _sft(train={"max_seq_length": LONG_SEQ_LENGTH, "grad_checkpoint": False})
    )
    assert not broken.ok
    assert any("grad_checkpoint" in error for error in broken.errors)

    fixed = validate(
        _sft(train={"max_seq_length": LONG_SEQ_LENGTH, "grad_checkpoint": True})
    )
    assert fixed.ok, fixed.render()
    assert any(str(LONG_SEQ_LENGTH) in warning for warning in fixed.warnings)


def test_with_long_context_turns_both_switches_at_once() -> None:
    cfg = with_long_context(load_config(SFT_PATH))
    assert cfg.train.max_seq_length == LONG_SEQ_LENGTH
    assert cfg.train.grad_checkpoint is True
    assert validate(cfg, train_rows=100).ok


@pytest.mark.parametrize("epochs", [1, 4, 10])
def test_the_sft_pass_is_pinned_to_the_specified_epoch_range(epochs: int) -> None:
    result = validate(_sft(train={"epochs": epochs}))
    assert not result.ok
    assert any("2-3 epochs" in error for error in result.errors)


def test_a_dpo_pass_without_a_resume_adapter_is_rejected() -> None:
    cfg = parse_config(
        {"pass": "dpo", "dpo": {"beta": 0.1}, "train": {"epochs": 1}}
    )
    result = validate(cfg)
    assert not result.ok
    assert any("resume_adapter" in error for error in result.errors)


def test_a_dpo_pass_warns_that_mlx_preference_support_is_version_dependent() -> None:
    result = validate(load_config(DPO_PATH), train_rows=100)
    assert result.ok
    assert any("mlx-lm is version-dependent" in warning for warning in result.warnings)


def test_an_sft_pass_may_not_carry_a_dpo_block() -> None:
    result = validate(_sft(dpo={"beta": 0.1}))
    assert not result.ok
    assert any("must not carry a [dpo] block" in error for error in result.errors)


def test_an_unverified_base_warns_rather_than_claiming_a_licence() -> None:
    result = validate(_sft(base_model="some-org/some-model"))
    assert any("licence-checked" in warning for warning in result.warnings)


def test_a_wrong_licence_claim_for_a_known_base_is_an_error() -> None:
    result = validate(_sft(base_model_licence="mit"))
    assert not result.ok
    assert any("inherits the base's licence" in error for error in result.errors)


def test_a_pinned_iters_that_contradicts_epochs_is_flagged() -> None:
    result = validate(_sft(train={"iters": 7}), train_rows=400)
    assert result.ok  # a pin is legal…
    assert any("epochs is a lie" in warning for warning in result.warnings)  # …and loud


def test_defaults_alone_produce_a_valid_sft_config() -> None:
    """The dataclass defaults and the YAML must not disagree."""
    cfg = AdapterConfig(
        pass_=TrainPass.SFT,
        lora=LoRASpec(),
        train=TrainSpec(),
        holdout=HoldoutSpec(),
    )
    assert validate(cfg, train_rows=100).ok
    assert cfg.train.learning_rate == load_config(SFT_PATH).train.learning_rate
    assert DPOSpec().beta == 0.1


def test_a_config_file_that_is_not_a_mapping_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="mapping at the top level"):
        load_config(path)


def test_a_missing_config_file_names_itself(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read adapter config"):
        load_config(tmp_path / "nope.yaml")
