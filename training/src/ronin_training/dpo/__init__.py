"""DPO: the preference-pair builder, the trainer profile, and the behavioural eval hooks.

Fixes the behaviours SFT can't teach — looping, giving up, ignoring denials, blind edits —
with prefix-identical (rejected, chosen) pairs across seven classes, a gentle pass onto the
SFT adapter (lr ~20x below SFT, SFT as the frozen reference), and hooks that measure
recovery-after-failure and loop rate on the holdout. Pure and offline-tested here; the GPU
DPO run and the real gate (recovery up, loop down, no eval-suite regression) run separately.

``ronin_training.dpo.train`` is the entrypoint (``python -m …dpo.train --check``) and is not
re-exported here, so ``-m …dpo.train`` does not warn about a double import.
"""

from __future__ import annotations

from ronin_training.dpo.eval_hook import (
    LOOP_THRESHOLD,
    LoopRate,
    RecoveryScore,
    loop_rate,
    recovery_rate,
    rollout_looped,
)
from ronin_training.dpo.pairs import (
    Pair,
    PairError,
    PreferenceClass,
    balance,
    class_counts,
    correct_turn,
    pair_from_rejection,
)
from ronin_training.dpo.profile import (
    LOSSES,
    MAX_DPO_LR,
    QWEN7B_DPO,
    DPOProfile,
    DPOProfileError,
)

__all__ = [
    "LOOP_THRESHOLD",
    "LOSSES",
    "MAX_DPO_LR",
    "QWEN7B_DPO",
    "DPOProfile",
    "DPOProfileError",
    "LoopRate",
    "Pair",
    "PairError",
    "PreferenceClass",
    "RecoveryScore",
    "balance",
    "class_counts",
    "correct_turn",
    "loop_rate",
    "pair_from_rejection",
    "recovery_rate",
    "rollout_looped",
]
