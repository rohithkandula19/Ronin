"""The adapter's training configuration: one schema, two backends, no invented values.

This module does NOT train. It reads Ronin's own YAML config, validates it, and
projects it into the two vocabularies that actually run:

* ``to_mlx_config()`` / ``to_mlx_argv()`` — ``mlx_lm.lora`` (Apple silicon).
* ``to_peft_kwargs()`` — ``peft`` + ``trl`` (the free Colab/Kaggle T4 lane that
  ``training/cuda/train_cuda.py`` already drives).

Why a Ronin-shaped config rather than mlx-lm's own YAML: the two backends disagree
about almost every key name, and about one thing that is not a name at all — PEFT
scales a LoRA update by ``alpha / rank`` while mlx-lm takes the scale directly, so
the same adapter written in mlx-lm's vocabulary needs ``scale: 2.0``, not
``alpha: 32``. Encoding that once, here, with a test, is the difference between two
runs of "the same" config producing the same adapter and producing two.

Validation is **structural and offline**: nothing here imports ``mlx``, ``torch`` or
``peft``. It checks the config against this schema and against the *documented* flag
and key vocabulary of ``mlx_lm.lora`` (mlx-lm >= 0.20), which is the most a machine
with no GPU can honestly check. :func:`validate` separates errors (the run is
misconfigured) from warnings (the run may not fit, or depends on a backend version
we cannot probe from here).

The hyperparameters are the user's, not derived here. Where the user specified a
range ("2-3 epochs") the config carries a concrete value and the validator enforces
the range, so a value drifting out of it is an error rather than a surprise.
"""
from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# The pinned base model
# --------------------------------------------------------------------------- #

#: HF repo for the peft/trl lane. Apache-2.0, which is why this base and not a
#: Llama or Gemma derivative: the adapter inherits the base's licence, and a
#: non-commercial base would make the published adapter non-commercial too.
BASE_MODEL = "Qwen/Qwen2.5-Coder-1.5B-Instruct"
#: Pre-quantized MLX repo for the Apple-silicon lane. LoRA over a 4-bit base is
#: QLoRA; there is no separate flag for it.
BASE_MODEL_MLX = "mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit"
#: The base's licence, stated so a reviewer does not have to go and check.
BASE_MODEL_LICENCE = "apache-2.0"

#: Bases whose licence this repo has actually verified. A config naming anything
#: else gets a warning rather than an error — it may well be fine, but nobody
#: here checked, and an unverified licence must not look verified.
VERIFIED_BASES: Mapping[str, str] = {
    BASE_MODEL: BASE_MODEL_LICENCE,
    BASE_MODEL_MLX: BASE_MODEL_LICENCE,
}

# --------------------------------------------------------------------------- #
# The dialect the adapter is trained to emit
# --------------------------------------------------------------------------- #

#: ``src/ronin``'s format shim parses calls out of these tags, and *only* these.
#: They are duplicated here rather than imported because ``ronin_training`` does
#: not depend on ``ronin``; ``test_adapter_config.py`` imports the real shim and
#: asserts the two agree, so a rename over there fails a test over here instead
#: of silently training the adapter on a tag the runtime will never parse.
#:
#: This is NOT the tag ``packages/dialect`` uses (bare ``<tool_call>``, for the
#: v1 ronin-code-1.5b model). The two dialects coexist in this repo and an
#: adapter trained on the wrong one scores zero on tool-syntax validity.
DIALECT_OPEN_TAG = "<ronin:tool_call>"
DIALECT_CLOSE_TAG = "</ronin:tool_call>"

# --------------------------------------------------------------------------- #
# LoRA
# --------------------------------------------------------------------------- #

#: Attention projections. Training q/k/v/o is what teaches the model *where to
#: look* in a long harness prompt (which tool, which argument).
ATTENTION_TARGETS: tuple[str, ...] = ("q_proj", "k_proj", "v_proj", "o_proj")
#: MLP projections. Training gate/up/down is what teaches the model *what to
#: write* — the literal shape of a call. Attention-only LoRA reliably picks the
#: right tool and still mis-serializes the arguments.
MLP_TARGETS: tuple[str, ...] = ("gate_proj", "up_proj", "down_proj")
#: Every target the user specified. The validator requires all seven: dropping
#: one silently changes what the adapter can learn.
REQUIRED_TARGETS: tuple[str, ...] = ATTENTION_TARGETS + MLP_TARGETS

#: PEFT module name → the suffix mlx-lm matches on. mlx-lm keys are qualified by
#: the parent block, PEFT's are not.
_MLX_KEY_PREFIX: Mapping[str, str] = {
    "q_proj": "self_attn",
    "k_proj": "self_attn",
    "v_proj": "self_attn",
    "o_proj": "self_attn",
    "gate_proj": "mlp",
    "up_proj": "mlp",
    "down_proj": "mlp",
}


@dataclass(frozen=True, slots=True)
class LoRASpec:
    """The adapter's shape. Rank and alpha are the user's numbers."""

    rank: int = 16
    alpha: int = 32
    #: Not user-specified. Carried over from the value already in this repo
    #: (``training/cuda/train_cuda.py``) rather than picked here.
    dropout: float = 0.05
    target_modules: tuple[str, ...] = REQUIRED_TARGETS
    #: How many transformer blocks get an adapter, counted from the top.
    #: ``-1`` means all of them, which is what mlx-lm reads it as.
    num_layers: int = -1

    @property
    def mlx_scale(self) -> float:
        """``alpha / rank`` — PEFT's effective scaling, which mlx-lm takes directly.

        The one conversion in this file that is arithmetic rather than renaming,
        and the one that silently halves or doubles the update if you skip it.
        """
        return self.alpha / self.rank

    def mlx_keys(self) -> tuple[str, ...]:
        """``target_modules`` in mlx-lm's qualified form, in the declared order."""
        return tuple(
            f"{_MLX_KEY_PREFIX[name]}.{name}" if name in _MLX_KEY_PREFIX else name
            for name in self.target_modules
        )


# --------------------------------------------------------------------------- #
# The optimiser block
# --------------------------------------------------------------------------- #

#: The user's sequence length. 4k covers a Ronin turn with a repo map and a
#: handful of tool results.
SEQ_LENGTH = 4096
#: The documented opt-in. 8k fits more of a long recovery transcript in one
#: example, and needs gradient checkpointing to fit anywhere — which the
#: validator enforces rather than switching on for you.
LONG_SEQ_LENGTH = 8192

#: Schedules this config knows how to project into both backends.
SCHEDULES = ("cosine", "linear", "constant")

#: The user's epoch range for the SFT pass.
SFT_EPOCH_RANGE = (2, 3)


@dataclass(frozen=True, slots=True)
class TrainSpec:
    """Optimiser settings shared by both passes."""

    epochs: int = SFT_EPOCH_RANGE[1]
    learning_rate: float = 1e-5
    schedule: str = "cosine"
    #: Not user-specified; carried over from ``training/cuda/train_cuda.py``.
    warmup_ratio: float = 0.03
    batch_size: int = 1
    grad_accum: int = 4
    max_seq_length: int = SEQ_LENGTH
    grad_checkpoint: bool = True
    seed: int = 0
    steps_per_eval: int = 50
    save_every: int = 100
    #: mlx-lm counts iterations, not epochs. ``0`` means "derive it from the row
    #: count", which :meth:`AdapterConfig.iters_for` does — refusing to guess a
    #: row count is why this is not simply defaulted to a number.
    iters: int = 0

    @property
    def effective_batch(self) -> int:
        return self.batch_size * self.grad_accum


@dataclass(frozen=True, slots=True)
class DPOSpec:
    """The preference pass. ``beta`` is the user's number.

    There is deliberately no learning rate or epoch count here: the user
    specified only ``beta`` and one epoch, so the pass reuses the shared
    :class:`TrainSpec` (with ``epochs: 1``) rather than inventing a second
    optimiser setting that nobody asked for.
    """

    beta: float = 0.1


# --------------------------------------------------------------------------- #
# The holdout
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class HoldoutSpec:
    """How the evaluation split is carved out. ``by`` is the load-bearing field.

    ``by: task`` means whole tasks go to one side or the other. ``by: example``
    would put two examples from the same task in train and test, and the reported
    score would then be partly a memorisation score. The validator rejects it by
    name for exactly that reason.
    """

    by: str = "task"
    fraction: float = 0.10
    seed: int = 0


# --------------------------------------------------------------------------- #
# The config
# --------------------------------------------------------------------------- #


class TrainPass(StrEnum):
    SFT = "sft"
    DPO = "dpo"


class ConfigError(ValueError):
    """A config could not be read or parsed at all.

    Distinct from a :class:`Validation` error: this is a file we could not turn
    into an :class:`AdapterConfig`, that is an :class:`AdapterConfig` that should
    not be run.
    """


@dataclass(frozen=True, slots=True)
class AdapterConfig:
    """One training pass, fully specified."""

    pass_: TrainPass
    base_model: str = BASE_MODEL
    base_model_mlx: str = BASE_MODEL_MLX
    base_model_licence: str = BASE_MODEL_LICENCE
    data_dir: str = "training/data/adapter"
    adapter_out: str = "training/adapters/ronin-adapter-qwen1.5b/sft"
    #: The adapter this pass starts from. Empty for SFT (it starts from the base);
    #: required for DPO, because DPO over the *base* model is a different
    #: experiment from DPO over the SFT checkpoint, and the difference is invisible
    #: in the resulting directory.
    resume_adapter: str = ""
    lora: LoRASpec = field(default_factory=LoRASpec)
    train: TrainSpec = field(default_factory=TrainSpec)
    dpo: DPOSpec | None = None
    holdout: HoldoutSpec = field(default_factory=HoldoutSpec)
    dialect_open_tag: str = DIALECT_OPEN_TAG
    dialect_close_tag: str = DIALECT_CLOSE_TAG

    # -- projections ------------------------------------------------------- #

    def iters_for(self, train_rows: int) -> int:
        """Epochs → mlx-lm iterations, given a real row count.

        Takes the row count as an argument instead of reading it from a file so
        the arithmetic is testable and so nothing here ever assumes a dataset
        size it has not been told.
        """
        if train_rows <= 0:
            raise ConfigError(
                "iters_for() needs the real number of training rows; "
                f"got {train_rows} — count the split first"
            )
        if self.train.iters > 0:
            return self.train.iters
        steps = math.ceil(train_rows / self.train.effective_batch)
        return max(1, steps * self.train.epochs)

    def to_mlx_config(self, *, train_rows: int | None = None) -> dict[str, Any]:
        """The dict to write as ``mlx_lm.lora -c config.yaml``.

        Raises rather than defaulting when it cannot compute ``iters``: a wrong
        iteration count is a run that trains for the wrong length and reports a
        loss curve that looks fine.
        """
        if self.train.iters > 0:
            iters = self.train.iters
        elif train_rows is not None:
            iters = self.iters_for(train_rows)
        else:
            raise ConfigError(
                "the mlx projection needs an iteration count: set train.iters in "
                "the config, or pass train_rows= so epochs can be converted"
            )
        warmup = math.ceil(self.train.warmup_ratio * iters)
        out: dict[str, Any] = {
            "model": self.base_model_mlx,
            "train": True,
            "data": self.data_dir,
            "fine_tune_type": "lora",
            "num_layers": self.lora.num_layers,
            "batch_size": self.train.batch_size,
            "iters": iters,
            "learning_rate": self.train.learning_rate,
            "max_seq_length": self.train.max_seq_length,
            "grad_checkpoint": self.train.grad_checkpoint,
            "seed": self.train.seed,
            "steps_per_eval": self.train.steps_per_eval,
            "save_every": self.train.save_every,
            "adapter_path": self.adapter_out,
            "lora_parameters": {
                "keys": list(self.lora.mlx_keys()),
                "rank": self.lora.rank,
                "scale": self.lora.mlx_scale,
                "dropout": self.lora.dropout,
            },
        }
        if self.resume_adapter:
            out["resume_adapter_file"] = self.resume_adapter
        if self.train.schedule == "cosine":
            # `arguments` is [init, decay_steps, end] for mlx-lm's cosine_decay.
            # The floor is 0.0 — the standard cosine end point, not a tuned value.
            out["lr_schedule"] = {
                "name": "cosine_decay",
                "warmup": warmup,
                "arguments": [self.train.learning_rate, iters, 0.0],
            }
        if self.pass_ is TrainPass.DPO and self.dpo is not None:
            out["training_mode"] = "dpo"
            out["beta"] = self.dpo.beta
        return out

    def to_mlx_argv(self, *, config_path: str) -> list[str]:
        """The argv that runs :meth:`to_mlx_config` from a written file.

        Everything that matters lives in the config file; the argv only points at
        it. That is on purpose — ``rank``, ``scale``, ``keys`` and the schedule
        have no CLI flags in mlx-lm, so a config expressed purely as argv silently
        trains r=8 attention-only LoRA with the default schedule.
        """
        return ["python", "-m", "mlx_lm.lora", "-c", config_path]

    def to_peft_kwargs(self) -> dict[str, dict[str, Any]]:
        """Keyword blocks for ``peft.LoraConfig`` / ``trl.SFTConfig`` / ``trl.DPOConfig``.

        Returned as plain dicts rather than constructed objects so this module
        stays importable with no ML stack installed.
        """
        out: dict[str, dict[str, Any]] = {
            "lora": {
                "r": self.lora.rank,
                "lora_alpha": self.lora.alpha,
                "lora_dropout": self.lora.dropout,
                "target_modules": list(self.lora.target_modules),
                "task_type": "CAUSAL_LM",
            },
            "train": {
                "num_train_epochs": self.train.epochs,
                "learning_rate": self.train.learning_rate,
                "lr_scheduler_type": self.train.schedule,
                "warmup_ratio": self.train.warmup_ratio,
                "per_device_train_batch_size": self.train.batch_size,
                "gradient_accumulation_steps": self.train.grad_accum,
                "max_length": self.train.max_seq_length,
                "gradient_checkpointing": self.train.grad_checkpoint,
                "seed": self.train.seed,
                "output_dir": self.adapter_out,
            },
        }
        if self.pass_ is TrainPass.DPO and self.dpo is not None:
            out["dpo"] = {"beta": self.dpo.beta, **out["train"]}
        return out


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

_TOP_KEYS = frozenset(
    {
        "pass",
        "base_model",
        "base_model_mlx",
        "base_model_licence",
        "data_dir",
        "adapter_out",
        "resume_adapter",
        "lora",
        "train",
        "dpo",
        "holdout",
        "dialect_open_tag",
        "dialect_close_tag",
    }
)
_LORA_KEYS = frozenset({"rank", "alpha", "dropout", "target_modules", "num_layers"})
_TRAIN_KEYS = frozenset(
    {
        "epochs",
        "learning_rate",
        "schedule",
        "warmup_ratio",
        "batch_size",
        "grad_accum",
        "max_seq_length",
        "grad_checkpoint",
        "seed",
        "steps_per_eval",
        "save_every",
        "iters",
    }
)
_DPO_KEYS = frozenset({"beta"})
_HOLDOUT_KEYS = frozenset({"by", "fraction", "seed"})


def _reject_unknown(section: str, data: Mapping[str, Any], allowed: Iterable[str]) -> None:
    """Unknown keys are fatal, not ignored.

    A tolerant parser turns ``lr: 2e-4`` (instead of ``learning_rate``) into a run
    that trains at 1e-5 and a config file that claims 2e-4. That is precisely the
    "someone changed the lr and nobody noticed" failure this phase exists to stop.
    """
    unknown = sorted(set(data) - set(allowed))
    if unknown:
        raise ConfigError(
            f"unknown key(s) in [{section}]: {unknown} — known keys are "
            f"{sorted(allowed)}. A misspelled key would silently keep the default."
        )


def _table(data: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = data.get(key)
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ConfigError(f"[{key}] must be a mapping, got {type(value).__name__}")
    return value


def parse_config(data: Mapping[str, Any]) -> AdapterConfig:
    """Build an :class:`AdapterConfig` from already-parsed YAML/JSON."""
    if not isinstance(data, Mapping):  # pragma: no cover - defensive
        raise ConfigError("an adapter config must be a mapping at the top level")
    _reject_unknown("config", data, _TOP_KEYS)

    raw_pass = str(data.get("pass", "")).strip()
    try:
        pass_ = TrainPass(raw_pass)
    except ValueError as exc:
        raise ConfigError(
            f"`pass` must be one of {[p.value for p in TrainPass]}, got {raw_pass!r}"
        ) from exc

    lora_raw = _table(data, "lora")
    _reject_unknown("lora", lora_raw, _LORA_KEYS)
    targets = lora_raw.get("target_modules", list(REQUIRED_TARGETS))
    if isinstance(targets, str) or not isinstance(targets, Sequence):
        raise ConfigError("lora.target_modules must be a list of module names")
    lora = LoRASpec(
        rank=int(lora_raw.get("rank", LoRASpec.rank)),
        alpha=int(lora_raw.get("alpha", LoRASpec.alpha)),
        dropout=float(lora_raw.get("dropout", LoRASpec.dropout)),
        target_modules=tuple(str(name) for name in targets),
        num_layers=int(lora_raw.get("num_layers", LoRASpec.num_layers)),
    )

    train_raw = _table(data, "train")
    _reject_unknown("train", train_raw, _TRAIN_KEYS)
    defaults = TrainSpec()
    train = TrainSpec(
        epochs=int(train_raw.get("epochs", defaults.epochs)),
        learning_rate=float(train_raw.get("learning_rate", defaults.learning_rate)),
        schedule=str(train_raw.get("schedule", defaults.schedule)),
        warmup_ratio=float(train_raw.get("warmup_ratio", defaults.warmup_ratio)),
        batch_size=int(train_raw.get("batch_size", defaults.batch_size)),
        grad_accum=int(train_raw.get("grad_accum", defaults.grad_accum)),
        max_seq_length=int(train_raw.get("max_seq_length", defaults.max_seq_length)),
        grad_checkpoint=bool(train_raw.get("grad_checkpoint", defaults.grad_checkpoint)),
        seed=int(train_raw.get("seed", defaults.seed)),
        steps_per_eval=int(train_raw.get("steps_per_eval", defaults.steps_per_eval)),
        save_every=int(train_raw.get("save_every", defaults.save_every)),
        iters=int(train_raw.get("iters", defaults.iters)),
    )

    dpo: DPOSpec | None = None
    if "dpo" in data and data["dpo"] is not None:
        dpo_raw = _table(data, "dpo")
        _reject_unknown("dpo", dpo_raw, _DPO_KEYS)
        dpo = DPOSpec(beta=float(dpo_raw.get("beta", DPOSpec.beta)))

    holdout_raw = _table(data, "holdout")
    _reject_unknown("holdout", holdout_raw, _HOLDOUT_KEYS)
    hd = HoldoutSpec()
    holdout = HoldoutSpec(
        by=str(holdout_raw.get("by", hd.by)),
        fraction=float(holdout_raw.get("fraction", hd.fraction)),
        seed=int(holdout_raw.get("seed", hd.seed)),
    )

    return AdapterConfig(
        pass_=pass_,
        base_model=str(data.get("base_model", BASE_MODEL)),
        base_model_mlx=str(data.get("base_model_mlx", BASE_MODEL_MLX)),
        base_model_licence=str(data.get("base_model_licence", BASE_MODEL_LICENCE)),
        data_dir=str(data.get("data_dir", AdapterConfig.data_dir)),
        adapter_out=str(data.get("adapter_out", AdapterConfig.adapter_out)),
        resume_adapter=str(data.get("resume_adapter", "")),
        lora=lora,
        train=train,
        dpo=dpo,
        holdout=holdout,
        dialect_open_tag=str(data.get("dialect_open_tag", DIALECT_OPEN_TAG)),
        dialect_close_tag=str(data.get("dialect_close_tag", DIALECT_CLOSE_TAG)),
    )


def load_config(path: str | Path) -> AdapterConfig:
    """Read a YAML (or JSON) adapter config from disk.

    ``yaml`` is imported here rather than at module scope so importing this
    package to inspect the dataclasses needs nothing installed.
    """
    file = Path(path)
    try:
        text = file.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read adapter config {file}: {exc}") from exc
    if file.suffix == ".json":
        try:
            parsed: Any = json.loads(text)
        except ValueError as exc:
            raise ConfigError(f"{file} is not valid JSON: {exc}") from exc
    else:
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover - pyyaml is a declared dep
            raise ConfigError(
                "reading a YAML adapter config needs pyyaml (`uv pip install pyyaml`); "
                "the same config can be given as .json, which needs nothing"
            ) from exc
        try:
            parsed = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise ConfigError(f"{file} is not valid YAML: {exc}") from exc
    if not isinstance(parsed, Mapping):
        raise ConfigError(f"{file} must contain a mapping at the top level")
    return parse_config(parsed)


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #

#: Keys ``mlx_lm.lora`` reads from a ``-c`` config file (mlx-lm >= 0.20). The
#: projection is checked against this so a key we renamed upstream shows up as a
#: validation error here rather than as a silently ignored setting there.
MLX_CONFIG_KEYS = frozenset(
    {
        "model",
        "train",
        "data",
        "fine_tune_type",
        "num_layers",
        "batch_size",
        "iters",
        "val_batches",
        "learning_rate",
        "steps_per_report",
        "steps_per_eval",
        "resume_adapter_file",
        "adapter_path",
        "save_every",
        "test",
        "test_batches",
        "max_seq_length",
        "grad_checkpoint",
        "seed",
        "lora_parameters",
        "lr_schedule",
        "mask_prompt",
        "wandb",
        # Preference training. Present in recent mlx-lm; see the warning
        # validate() emits — this list is documentation, not a probe.
        "training_mode",
        "beta",
    }
)
#: Flags ``mlx_lm.lora`` accepts on the command line.
MLX_ARGV_FLAGS = frozenset(
    {
        "-c",
        "--config",
        "--model",
        "--train",
        "--data",
        "--fine-tune-type",
        "--num-layers",
        "--batch-size",
        "--iters",
        "--learning-rate",
        "--max-seq-length",
        "--steps-per-eval",
        "--steps-per-report",
        "--save-every",
        "--seed",
        "--adapter-path",
        "--resume-adapter-file",
        "--grad-checkpoint",
        "--val-batches",
        "--test",
        "--test-batches",
        "--mask-prompt",
    }
)
_MLX_LORA_PARAM_KEYS = frozenset({"keys", "rank", "scale", "dropout"})


@dataclass(frozen=True, slots=True)
class Validation:
    """What is wrong with a config, split by whether it blocks the run.

    Warnings are separate because several honest problems are unknowable from
    here — whether a 4090 has room for an 8k sequence, whether the installed
    mlx-lm supports DPO. Folding them into errors would either block a run that
    is fine or teach people to ignore errors.
    """

    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors

    def render(self) -> str:
        lines: list[str] = []
        for item in self.errors:
            lines.append(f"ERROR  {item}")
        for item in self.warnings:
            lines.append(f"WARN   {item}")
        return "\n".join(lines) if lines else "ok"


def validate(cfg: AdapterConfig, *, train_rows: int | None = None) -> Validation:
    """Every reason ``cfg`` should not be run, and every reason to be careful."""
    errors: list[str] = []
    warnings: list[str] = []

    # -- the base model and its licence
    if not cfg.base_model:
        errors.append("base_model is required")
    verified = VERIFIED_BASES.get(cfg.base_model)
    if verified is None:
        warnings.append(
            f"base_model {cfg.base_model!r} is not one this repo has licence-checked "
            f"({sorted(VERIFIED_BASES)}) — verify its licence before publishing an "
            "adapter derived from it"
        )
    elif cfg.base_model_licence != verified:
        errors.append(
            f"base_model_licence says {cfg.base_model_licence!r} but "
            f"{cfg.base_model} is {verified!r} — the adapter inherits the base's "
            "licence, so this claim ships with the model card"
        )

    # -- LoRA shape
    if cfg.lora.rank <= 0:
        errors.append("lora.rank must be positive")
    if cfg.lora.alpha <= 0:
        errors.append("lora.alpha must be positive")
    if not 0.0 <= cfg.lora.dropout < 1.0:
        errors.append(f"lora.dropout must be in [0, 1), got {cfg.lora.dropout}")
    missing = [name for name in REQUIRED_TARGETS if name not in cfg.lora.target_modules]
    if missing:
        errors.append(
            f"lora.target_modules is missing {missing} — the specified target set is "
            f"{list(REQUIRED_TARGETS)}: attention alone picks the right tool and "
            "still mis-serializes its arguments"
        )
    unknown_targets = [
        name for name in cfg.lora.target_modules if name not in _MLX_KEY_PREFIX
    ]
    if unknown_targets:
        errors.append(
            f"lora.target_modules names {unknown_targets}, which have no mlx-lm key "
            "mapping — add them to _MLX_KEY_PREFIX or remove them"
        )
    if cfg.lora.num_layers == 0 or cfg.lora.num_layers < -1:
        errors.append("lora.num_layers must be a positive count or -1 for all layers")

    # -- optimiser
    if cfg.train.learning_rate <= 0:
        errors.append("train.learning_rate must be positive")
    if cfg.train.schedule not in SCHEDULES:
        errors.append(
            f"train.schedule must be one of {list(SCHEDULES)}, got {cfg.train.schedule!r}"
        )
    if cfg.train.batch_size <= 0 or cfg.train.grad_accum <= 0:
        errors.append("train.batch_size and train.grad_accum must both be positive")
    if not 0.0 <= cfg.train.warmup_ratio < 1.0:
        errors.append("train.warmup_ratio must be in [0, 1)")
    if cfg.train.max_seq_length <= 0 or cfg.train.max_seq_length % 512:
        errors.append(
            f"train.max_seq_length must be a positive multiple of 512, got "
            f"{cfg.train.max_seq_length}"
        )
    if cfg.train.max_seq_length > LONG_SEQ_LENGTH:
        errors.append(
            f"train.max_seq_length {cfg.train.max_seq_length} exceeds the documented "
            f"ceiling of {LONG_SEQ_LENGTH}; raise LONG_SEQ_LENGTH deliberately if a "
            "machine really has the room"
        )
    elif cfg.train.max_seq_length > SEQ_LENGTH:
        if not cfg.train.grad_checkpoint:
            errors.append(
                f"train.max_seq_length {cfg.train.max_seq_length} is the opt-in long "
                "context and needs train.grad_checkpoint: true — this is a toggle, "
                "not a default, because it trades ~30% throughput for the memory"
            )
        warnings.append(
            f"train.max_seq_length {cfg.train.max_seq_length} > {SEQ_LENGTH}: only "
            "run this where you have measured the memory headroom"
        )
    if cfg.train.batch_size > 1:
        warnings.append(
            "train.batch_size > 1 raises peak memory; a free T4 or an 8 GB Mac "
            "usually needs batch_size 1 with grad_accum for the effective batch"
        )
    if cfg.train.iters > 0 and train_rows is not None:
        derived = math.ceil(train_rows / cfg.train.effective_batch) * cfg.train.epochs
        if cfg.train.iters != derived:
            warnings.append(
                f"train.iters is pinned to {cfg.train.iters}, but {train_rows} rows at "
                f"effective batch {cfg.train.effective_batch} for {cfg.train.epochs} "
                f"epochs is {derived} iterations — the pin wins, so epochs is a lie"
            )

    # -- per-pass rules
    if cfg.pass_ is TrainPass.SFT:
        low, high = SFT_EPOCH_RANGE
        if not low <= cfg.train.epochs <= high:
            errors.append(
                f"the SFT pass is specified at {low}-{high} epochs, got "
                f"{cfg.train.epochs} — more is memorisation on a corpus this size"
            )
        if cfg.dpo is not None:
            errors.append("the SFT pass must not carry a [dpo] block")
        if cfg.resume_adapter:
            warnings.append(
                "the SFT pass declares resume_adapter, so it continues an existing "
                "adapter rather than starting from the base — intended?"
            )
    else:
        if cfg.dpo is None:
            errors.append("the DPO pass requires a [dpo] block with beta")
        elif cfg.dpo.beta <= 0:
            errors.append(f"dpo.beta must be positive, got {cfg.dpo.beta}")
        if cfg.train.epochs != 1:
            errors.append(
                f"the DPO pass is specified at 1 epoch, got {cfg.train.epochs} — "
                "preference pairs overfit fast"
            )
        if not cfg.resume_adapter:
            errors.append(
                "the DPO pass must set resume_adapter to the SFT checkpoint; DPO "
                "straight onto the base model is a different experiment and the "
                "output directory looks identical either way"
            )
        warnings.append(
            "preference training in mlx-lm is version-dependent: confirm "
            "`python -m mlx_lm.lora --help` lists a preference/DPO mode before "
            "running the mlx lane, or use the peft/trl lane (trl.DPOTrainer)"
        )

    # -- the holdout, which is the one that inflates a score if it is wrong
    if cfg.holdout.by != "task":
        errors.append(
            f"holdout.by must be 'task', got {cfg.holdout.by!r} — splitting by "
            "example puts two examples of the same task in train and test, and the "
            "reported score is then partly a memorisation score"
        )
    if not 0.0 < cfg.holdout.fraction < 0.5:
        errors.append(
            f"holdout.fraction must be in (0, 0.5), got {cfg.holdout.fraction}"
        )

    # -- the dialect
    if cfg.dialect_open_tag != DIALECT_OPEN_TAG or cfg.dialect_close_tag != DIALECT_CLOSE_TAG:
        errors.append(
            f"dialect tags are {cfg.dialect_open_tag!r}/{cfg.dialect_close_tag!r} but "
            f"Ronin's format shim parses {DIALECT_OPEN_TAG!r}/{DIALECT_CLOSE_TAG!r} "
            "and only those — an adapter trained on any other tag scores zero on "
            "tool-syntax validity"
        )

    # -- the mlx projection, checked structurally
    errors.extend(_mlx_projection_problems(cfg, train_rows=train_rows))
    return Validation(tuple(errors), tuple(warnings))


def _mlx_projection_problems(
    cfg: AdapterConfig, *, train_rows: int | None
) -> list[str]:
    """Check what mlx-lm would be handed, without importing mlx."""
    rows = train_rows if train_rows is not None else 1
    try:
        projected = cfg.to_mlx_config(train_rows=rows)
    except ConfigError as exc:
        return [f"the mlx projection cannot be built: {exc}"]
    problems: list[str] = []
    unknown = sorted(set(projected) - MLX_CONFIG_KEYS)
    if unknown:
        problems.append(
            f"the mlx projection emits key(s) mlx_lm.lora does not read: {unknown}"
        )
    params = projected.get("lora_parameters")
    if not isinstance(params, Mapping):
        problems.append("the mlx projection lost its lora_parameters block")
    else:
        bad = sorted(set(params) - _MLX_LORA_PARAM_KEYS)
        if bad:
            problems.append(f"lora_parameters carries unknown key(s) {bad}")
    argv = cfg.to_mlx_argv(config_path="x.yaml")
    # Skip the interpreter prefix (`python -m mlx_lm.lora`): its `-m` belongs to
    # Python, not to mlx-lm, and scanning it would flag every valid argv.
    tail = argv[argv.index("mlx_lm.lora") + 1 :] if "mlx_lm.lora" in argv else argv
    flags = [token for token in tail if token.startswith("-")]
    stray = sorted(flag for flag in flags if flag not in MLX_ARGV_FLAGS)
    if stray:
        problems.append(f"the mlx argv uses flag(s) mlx_lm.lora does not accept: {stray}")
    return problems


def dump_mlx_config(
    cfg: AdapterConfig, path: str | Path, *, train_rows: int | None = None
) -> Path:
    """Write the mlx-lm ``-c`` config file. Returns the path written."""
    out = Path(path)
    payload = cfg.to_mlx_config(train_rows=train_rows)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.suffix == ".json":
        out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return out
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - pyyaml is a declared dep
        raise ConfigError(
            "writing a YAML mlx config needs pyyaml; pass a .json path instead"
        ) from exc
    out.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")
    return out


def with_long_context(cfg: AdapterConfig) -> AdapterConfig:
    """The documented 8k toggle, applied as one call rather than two edits.

    Turning gradient checkpointing on at the same time is the whole point: 8k
    without it OOMs on every machine this pipeline targets, and a config that
    OOMs forty minutes in is worse than one that refuses up front.
    """
    return replace(
        cfg,
        train=replace(cfg.train, max_seq_length=LONG_SEQ_LENGTH, grad_checkpoint=True),
    )


#: Where the shipped configs live, relative to the repo root.
SFT_CONFIG_PATH = "training/config/adapter_sft.yaml"
DPO_CONFIG_PATH = "training/config/adapter_dpo.yaml"


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m ronin_training.adapter.config --check <config...>``."""
    import argparse

    parser = argparse.ArgumentParser(description="validate an adapter training config")
    parser.add_argument("configs", nargs="*", default=[SFT_CONFIG_PATH, DPO_CONFIG_PATH])
    parser.add_argument("--train-rows", type=int, default=None,
                        help="row count of the training split, for the iters check")
    parser.add_argument("--check", action="store_true", help="accepted for symmetry; always on")
    args = parser.parse_args(argv)
    del args.check

    paths = args.configs or [SFT_CONFIG_PATH, DPO_CONFIG_PATH]
    failed = False
    for raw in paths:
        try:
            cfg = load_config(raw)
        except ConfigError as exc:
            print(f"{raw}: UNREADABLE — {exc}")
            failed = True
            continue
        result = validate(cfg, train_rows=args.train_rows)
        status = "ok" if result.ok else "INVALID"
        print(f"{raw}: {status} ({cfg.pass_.value} pass, base {cfg.base_model})")
        if result.errors or result.warnings:
            print(result.render())
        failed = failed or not result.ok
    return 1 if failed else 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
