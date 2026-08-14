"""SFT: the assistant-only mask, the trainer profiles, the holdout eval hook, the dry-run.

Teaches Ronin's exact prompt format and rules, not coding knowledge. The data builder and
mask are pure and offline-tested here; the GPU training run (mlx-lm for 1.5b, trl/axolotl
for 7b) and the >0.97 holdout gate happen separately. Reuses the existing split-by-task
(``adapter.dataset``), prompt renderer (``harvest.prompt``), and tool-validity metric
(``adapter.metrics``) rather than re-implementing them.
"""

from __future__ import annotations

from ronin_training.sft.eval_hook import (
    GATE,
    ToolCall,
    ValidityReport,
    call_is_valid,
    calls_from_rows,
    tool_validity,
)
from ronin_training.sft.masking import (
    ASSISTANT_ROLE,
    IGNORE_INDEX,
    LabeledExample,
    Tokenizer,
    build_labels,
    default_render,
    learnable_row,
    masked_roles,
)
from ronin_training.sft.pipeline import SFTPlan, plan_sft_run
from ronin_training.sft.profiles import (
    QWEN1P5B_DEBUG,
    QWEN7B_QLORA,
    SFTProfile,
    SFTProfileError,
)

# NOTE: ``ronin_training.sft.train`` is an entrypoint (``python -m …sft.train``), not part
# of this package's import surface. Re-exporting it here would make ``-m …sft.train`` warn
# "found in sys.modules after import of package" and run module-level code twice — the same
# footgun ``adapter/__main__.py`` documents. Import ``check_run`` from ``sft.train`` directly.

__all__ = [
    "ASSISTANT_ROLE",
    "GATE",
    "IGNORE_INDEX",
    "QWEN1P5B_DEBUG",
    "QWEN7B_QLORA",
    "LabeledExample",
    "SFTPlan",
    "SFTProfile",
    "SFTProfileError",
    "Tokenizer",
    "ToolCall",
    "ValidityReport",
    "build_labels",
    "call_is_valid",
    "calls_from_rows",
    "default_render",
    "learnable_row",
    "masked_roles",
    "plan_sft_run",
    "tool_validity",
]
