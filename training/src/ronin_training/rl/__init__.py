"""Agentic RLVR: the environment, the verifiable reward, and the GRPO config.

The eval harness is the reward source — a rollout's score is what ``verify.sh`` did on a
HIDDEN held-out test, gated hard against editing the tests and running out of turns. The
GPU training loop (verl/SkyRL) runs separately and imports these pure, offline-tested
pieces; nothing here needs a GPU, a model, or a container to run its tests.
"""

from __future__ import annotations

from .config import CurriculumBand, GRPOConfig, RLConfigError, curriculum_keep
from .environment import (
    DEFAULT_MAX_TURNS,
    Environment,
    HiddenTest,
    Policy,
    PolicyResult,
    Rollout,
    Sandbox,
    bash_sandbox,
    solution_policy,
)
from .loss import assistant_token_mask, broadcast_reward, dapo_normalize
from .reward import (
    PROTECTED_FRAGMENTS,
    RewardBreakdown,
    RewardWeights,
    RolloutOutcome,
    compute_reward,
    protected_hit,
)

__all__ = [
    "DEFAULT_MAX_TURNS",
    "PROTECTED_FRAGMENTS",
    "CurriculumBand",
    "Environment",
    "GRPOConfig",
    "HiddenTest",
    "Policy",
    "PolicyResult",
    "RLConfigError",
    "RewardBreakdown",
    "RewardWeights",
    "Rollout",
    "RolloutOutcome",
    "Sandbox",
    "assistant_token_mask",
    "bash_sandbox",
    "broadcast_reward",
    "compute_reward",
    "curriculum_keep",
    "dapo_normalize",
    "protected_hit",
    "solution_policy",
]
