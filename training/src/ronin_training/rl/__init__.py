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
from .guards import (
    DETECTORS,
    FileChange,
    Finding,
    GuardConfig,
    GuardReport,
    GuardRollout,
    Severity,
    TranscriptSampler,
    TranscriptStep,
    changes_from_trees,
    render_transcript,
    sample_transcripts,
    scan_rollout,
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
    "DETECTORS",
    "PROTECTED_FRAGMENTS",
    "CurriculumBand",
    "Environment",
    "FileChange",
    "Finding",
    "GRPOConfig",
    "GuardConfig",
    "GuardReport",
    "GuardRollout",
    "HiddenTest",
    "Policy",
    "PolicyResult",
    "RLConfigError",
    "RewardBreakdown",
    "RewardWeights",
    "Rollout",
    "RolloutOutcome",
    "Sandbox",
    "Severity",
    "TranscriptSampler",
    "TranscriptStep",
    "assistant_token_mask",
    "bash_sandbox",
    "broadcast_reward",
    "changes_from_trees",
    "compute_reward",
    "curriculum_keep",
    "dapo_normalize",
    "protected_hit",
    "render_transcript",
    "sample_transcripts",
    "scan_rollout",
    "solution_policy",
]
