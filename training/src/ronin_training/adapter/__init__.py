"""ronin-adapter-qwen1.5b: the pipeline, the honest three-way eval, and nothing trained.

**What the adapter teaches, and what it does not.** It teaches a small open model to
be *ronin-native*: emit a `<ronin:tool_call>` block Ronin's format shim can parse,
respect the approval gate instead of arguing with it, adapt after a failed
`ToolResult` instead of re-issuing the same call, and plan in the harness's shape.
It **does not add coding knowledge.** A 1.5B model that has learned Ronin's protocol
perfectly is still a 1.5B model; the fine-tune moves protocol compliance, not
reasoning. That disclaimer is repeated in the model card and in the README because a
reviewer who is not told this will assume the opposite claim.

The five pieces:

* :mod:`~ronin_training.adapter.config` — the training configuration for both
  passes, validated structurally offline and projected into ``mlx_lm.lora`` and
  ``peft``/``trl`` vocabularies. Nothing here imports an ML stack.
* :mod:`~ronin_training.adapter.dataset` — the holdout split, **by task and never by
  example**, with the no-leak check asserted on its own output.
* :mod:`~ronin_training.adapter.metrics` — tool-syntax validity and recovery rate,
  defined once, as arithmetic over classified turns.
* :mod:`~ronin_training.adapter.threeway` — adapter vs base Qwen vs Kimi on the same
  phase-11 suite, with "the adapter lost" as a first-class rendered outcome.
* :mod:`~ronin_training.adapter.preflight` — the one command the free-training path
  calls before a GPU-hour is spent: validate, split, assert no task leaked, print the
  per-backend settings into the run's own log.

**No number produced by this package exists unless a run produced it.** Rates are
``float | None`` and render as ``—`` when the denominator was zero. This environment
has no GPU and ``mlx-lm`` is Apple-silicon-only, so no training run happened here and
no artifact in this tree carries a score from one.
"""

from __future__ import annotations

from .config import (
    ATTENTION_TARGETS,
    BASE_MODEL,
    BASE_MODEL_LICENCE,
    BASE_MODEL_MLX,
    DIALECT_CLOSE_TAG,
    DIALECT_OPEN_TAG,
    DPO_CONFIG_PATH,
    GRPO_CONFIG_PATH,
    KNOWN_REWARDS,
    LONG_SEQ_LENGTH,
    MLP_TARGETS,
    MLX_ARGV_FLAGS,
    MLX_CONFIG_KEYS,
    REQUIRED_TARGETS,
    SEQ_LENGTH,
    SFT_CONFIG_PATH,
    SFT_EPOCH_RANGE,
    V1_DIALECT_CLOSE_TAG,
    V1_DIALECT_OPEN_TAG,
    AdapterConfig,
    ConfigError,
    DPOSpec,
    GRPOSpec,
    HoldoutSpec,
    LoRASpec,
    TrainPass,
    TrainSpec,
    Validation,
    dump_mlx_config,
    load_config,
    parse_config,
    validate,
    with_long_context,
)
from .dataset import (
    DPO_REQUIRED,
    SFT_REQUIRED,
    TASK_ID_KEYS,
    DatasetError,
    Example,
    TaskSplit,
    examples_from_rows,
    read_examples,
    read_jsonl,
    split_by_task,
    task_id_of,
    verify_no_task_leak,
    write_split,
)
from .metrics import (
    NOT_MEASURED,
    VALID_CALL,
    CallCheck,
    MetricsError,
    RecoveryScore,
    Setback,
    SyntaxScore,
    TurnRecord,
    attempted_call,
    call_fingerprint,
    check_call,
    check_raw_call,
    find_call_blocks,
    load_tool_schemas,
    recovery_rate,
    tool_syntax_validity,
)
from .preflight import Preflight, preflight
from .preflight import render as render_preflight
from .reward import (
    DEFAULT_WEIGHTS,
    RewardFn,
    RewardWeights,
    TurnReward,
    group_advantages,
    make_reward_fn,
    score_turn,
)
from .threeway import (
    ADAPTER,
    BASE,
    ITERATE_STEPS,
    KIMI,
    METRIC_DEFINITIONS,
    TARGET_ORDER,
    Outcome,
    SuiteScore,
    TargetResult,
    TargetRun,
    ThreeWayError,
    ThreeWayReport,
    Verdict,
    assemble_report,
    evals_module,
    render_markdown,
    run_three_way,
    suite_score_of,
    target_runner,
    write_report,
)

__all__ = [
    "ADAPTER",
    "ATTENTION_TARGETS",
    "BASE",
    "BASE_MODEL",
    "BASE_MODEL_LICENCE",
    "BASE_MODEL_MLX",
    "DEFAULT_WEIGHTS",
    "DIALECT_CLOSE_TAG",
    "DIALECT_OPEN_TAG",
    "DPO_CONFIG_PATH",
    "DPO_REQUIRED",
    "GRPO_CONFIG_PATH",
    "ITERATE_STEPS",
    "KIMI",
    "KNOWN_REWARDS",
    "LONG_SEQ_LENGTH",
    "METRIC_DEFINITIONS",
    "MLP_TARGETS",
    "MLX_ARGV_FLAGS",
    "MLX_CONFIG_KEYS",
    "NOT_MEASURED",
    "REQUIRED_TARGETS",
    "SEQ_LENGTH",
    "SFT_CONFIG_PATH",
    "SFT_EPOCH_RANGE",
    "SFT_REQUIRED",
    "TARGET_ORDER",
    "TASK_ID_KEYS",
    "V1_DIALECT_CLOSE_TAG",
    "V1_DIALECT_OPEN_TAG",
    "VALID_CALL",
    "AdapterConfig",
    "CallCheck",
    "ConfigError",
    "DPOSpec",
    "DatasetError",
    "Example",
    "GRPOSpec",
    "HoldoutSpec",
    "LoRASpec",
    "MetricsError",
    "Outcome",
    "Preflight",
    "RecoveryScore",
    "RewardFn",
    "RewardWeights",
    "Setback",
    "SuiteScore",
    "SyntaxScore",
    "TargetResult",
    "TargetRun",
    "TaskSplit",
    "ThreeWayError",
    "ThreeWayReport",
    "TrainPass",
    "TrainSpec",
    "TurnRecord",
    "TurnReward",
    "Validation",
    "Verdict",
    "assemble_report",
    "attempted_call",
    "call_fingerprint",
    "check_call",
    "check_raw_call",
    "dump_mlx_config",
    "evals_module",
    "examples_from_rows",
    "find_call_blocks",
    "group_advantages",
    "load_config",
    "load_tool_schemas",
    "make_reward_fn",
    "parse_config",
    "preflight",
    "read_examples",
    "read_jsonl",
    "recovery_rate",
    "render_markdown",
    "render_preflight",
    "run_three_way",
    "score_turn",
    "split_by_task",
    "suite_score_of",
    "target_runner",
    "task_id_of",
    "tool_syntax_validity",
    "validate",
    "verify_no_task_leak",
    "with_long_context",
    "write_report",
    "write_split",
]
