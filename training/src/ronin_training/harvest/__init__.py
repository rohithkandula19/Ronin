"""Harvest recorded runs into training data that teaches *behaviour*, not knowledge.

**What the adapter this feeds is for, and what it is explicitly not for.** The target
is a small open model made *ronin-native*: correct tool syntax, correct approval-gate
behaviour, correct recovery from a refusal or an error, correct planning. It is **not**
a coding model, and nothing in this pipeline attempts to teach coding knowledge. That
disclaimer is not modesty — it decides the design of both halves of this package:

* **extraction selects for behaviour.** An example is cut at a turn boundary, its
  target is the *action* a passing run took, and turns that call no tool are dropped by
  default because prose is the one thing the base model already does adequately.
* **augmentation destroys memorizable specifics.** Paths, module names, symbols and
  directory shapes are renamed consistently, so a 1.5B model cannot spend its capacity
  memorising forty fixture filenames instead of learning the protocol.

Nothing here invents a prompt format. The tool-call wire text comes from
``ronin_dialect`` — the canonical module the runtime parser and the eval extractor
already share — and rows are the ``{"messages", "tools"}`` pair the trainer hands to
the tokenizer's own ``apply_chat_template``. The reason is written down in
``training/reports/valid_tool_json_root_cause.md``: three implementations of one
dialect that merely happened to agree, plus a training prompt that presented a
different tool distribution than inference, is how tool syntax scored 0/4.

Two halves:

``sft``
    Successful trajectories -> ``(system + tools + history) -> assistant turn``.
    Gated on ``verify.sh`` passing **and** a turn count within 1.5x the median for
    that task, because a bad-but-passing run teaches bad habits. Every drop is counted.

``negatives`` + ``dpo``
    Failures mined into four named classes — malformed tool json, ignoring an approval
    denial, looping, editing without reading — and paired with a corrected turn. The
    correction is a real turn from a passing run where one exists, and where none does
    it is constructed from the violated rule and flagged ``synthesized_chosen``. Some
    negatives have no honest correction; those emit no pair and are counted.

Provenance is on every row: task, run, model, whether it was augmented and with which
seed, and where the preferred side came from. Real-session harvesting is opt-in and
cannot read a directory it was not handed — see :class:`~.sources.SessionHarvest`.

Run the whole thing offline: ``python -m ronin_training.harvest.demo``.
"""
from __future__ import annotations

from .augment import Renaming, augment, plan_renaming
from .dpo import (
    SCHEMA_PATH,
    ChatRenderer,
    ChosenTurn,
    PairCounts,
    PreferencePair,
    build_pairs,
    find_donor,
    load_schema,
    synthesize,
    to_text_row,
    validate_row,
)
from .dpo import write_jsonl as write_pairs_jsonl
from .filters import (
    TURN_BUDGET_FACTOR,
    FilterCounts,
    TaskBudget,
    budget_table,
    select,
    turn_budgets,
)
from .negatives import (
    DEFAULT_TOOL_ROLES,
    LOOP_REPEATS,
    LOOP_WINDOW,
    Negative,
    NegativeClass,
    ToolRoles,
    class_counts,
    mine,
    mine_all,
)
from .prompt import (
    PromptPayload,
    assistant_message,
    assistant_target_text,
    parse_target_text,
    render_prefix,
    tools_block,
)
from .provenance import Provenance, SourceKind
from .sft import (
    DEFAULT_MAX_EXAMPLES,
    TARGET_MAX_EXAMPLES,
    TARGET_MIN_EXAMPLES,
    HarvestCounts,
    SftConfig,
    SftExample,
    extract,
)
from .sft import write_jsonl as write_examples_jsonl
from .sources import (
    HarvestError,
    RunManifest,
    SessionHarvest,
    TranscriptReader,
    load_run,
    load_runs,
    load_sessions,
    steps_from_events,
)
from .trajectory import (
    DENIAL_PREFIX,
    RecordedCall,
    RecordedResult,
    Step,
    ToolSchema,
    Trajectory,
    fingerprint,
)

__all__ = [
    "DEFAULT_MAX_EXAMPLES",
    "DEFAULT_TOOL_ROLES",
    "DENIAL_PREFIX",
    "LOOP_REPEATS",
    "LOOP_WINDOW",
    "SCHEMA_PATH",
    "TARGET_MAX_EXAMPLES",
    "TARGET_MIN_EXAMPLES",
    "TURN_BUDGET_FACTOR",
    "ChatRenderer",
    "ChosenTurn",
    "FilterCounts",
    "HarvestCounts",
    "HarvestError",
    "Negative",
    "NegativeClass",
    "PairCounts",
    "PreferencePair",
    "PromptPayload",
    "Provenance",
    "RecordedCall",
    "RecordedResult",
    "Renaming",
    "RunManifest",
    "SessionHarvest",
    "SftConfig",
    "SftExample",
    "SourceKind",
    "Step",
    "TaskBudget",
    "ToolRoles",
    "ToolSchema",
    "Trajectory",
    "TranscriptReader",
    "assistant_message",
    "assistant_target_text",
    "augment",
    "budget_table",
    "build_pairs",
    "class_counts",
    "extract",
    "find_donor",
    "fingerprint",
    "load_run",
    "load_runs",
    "load_schema",
    "load_sessions",
    "mine",
    "mine_all",
    "parse_target_text",
    "plan_renaming",
    "render_prefix",
    "select",
    "steps_from_events",
    "synthesize",
    "to_text_row",
    "tools_block",
    "turn_budgets",
    "validate_row",
    "write_examples_jsonl",
    "write_pairs_jsonl",
]
