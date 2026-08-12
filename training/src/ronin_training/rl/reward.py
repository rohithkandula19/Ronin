"""The RLVR reward — the eval harness *is* the reward source, turned into a scalar.

GRPO needs a number for every rollout. For an agent whose thesis is "did it actually fix
the code", that number must come from **running the code**, not from a learned reward
model's opinion — so a rollout's reward is a deterministic function of what
``verify.sh`` did and how the agent got there. This module is that function, and nothing
in it imports ``torch``/``trl``/``vllm``: it is pure Python over a
:class:`RolloutOutcome`, unit-tested with no GPU and no model.

The design is defensive on purpose, because a verifiable reward is exactly the thing a
policy learns to *hack*. Three rules encode that:

1. **Hard gates come first and short-circuit.** Editing the tests or the verifier to make
   a red suite go green is the single most tempting exploit, so a diff that touches a
   test file or ``verify.sh`` scores :attr:`RewardWeights.touched_protected` (``-1.0``)
   and *nothing else is computed* — a gamed pass can never out-earn honest work. Running
   out of turns scores :attr:`RewardWeights.ran_out_of_turns` (``-0.5``): a real cost, so
   flailing is worse than stopping, but not as bad as cheating.
2. **The pass signal is the HIDDEN test, never the visible one.** ``base`` is ``+1.0``
   iff :attr:`RolloutOutcome.verify_passed_hidden` — the held-out test file the agent
   never saw during the rollout (see :mod:`ronin_training.rl.environment`). Keying the
   reward on the visible suite would pay a model that overfits to the exact assertions in
   front of it; keying it on a suite it could not read is what makes "pass" mean
   "generalised". :attr:`RolloutOutcome.verify_passed_visible` is carried for reporting
   the gap, and is deliberately absent from the arithmetic.
3. **Shaping is capped below the task signal.** The +0.1·tool-validity / −0.05·redundant
   / −0.1·stated-done-while-failing / +0.05·under-median terms exist to break ties
   between two passing rollouts, not to be an objective of their own — a model that finds
   it can farm shaping instead of fixing the bug is a training run wasted. So their sum is
   clamped to ±:attr:`RewardWeights.shaping_cap` (``0.15``) *before* it is added to the
   ``base``, which is ``1.0``. The clamp is load-bearing: −0.05 per redundant call is
   unbounded otherwise, and twenty pointless greps would swamp a real fix without it.

The final reward is clipped to ``[-1.0, 1.3]``. The ceiling is slack (best honest reward
is ``1.0 + 0.15 = 1.15``); the floor coincides with the cheat gate, so "edited the tests"
and "the worst possible run" are the same depth and no lower.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

#: Path fragments that mark a file the agent must not edit to earn its reward: any test
#: file, a pytest ``conftest``, or the verifier itself. Matched against each changed path,
#: case-insensitively, as a substring of the final component or a parent directory — a
#: rollout that touches one of these is gated regardless of what the suite then reports.
PROTECTED_FRAGMENTS: Final[tuple[str, ...]] = (
    "verify.sh",
    "conftest.py",
)


def _is_protected(path: str, extra: Sequence[str]) -> bool:
    """Whether ``path`` is a test file, the verifier, or a caller-named held-out file."""
    lowered = path.replace("\\", "/").lower()
    name = lowered.rsplit("/", 1)[-1]
    if name.startswith("test_") or name.endswith("_test.py"):
        return True
    if "/tests/" in lowered or lowered.startswith("tests/"):
        return True
    if any(fragment.lower() in lowered for fragment in (*PROTECTED_FRAGMENTS, *extra)):
        return True
    return False


def protected_hit(paths: Sequence[str], *, extra: Sequence[str] = ()) -> str | None:
    """The first changed path that is off-limits, or ``None``. The anti-cheat gate's eye."""
    for path in paths:
        if _is_protected(path, extra):
            return path
    return None


@dataclass(frozen=True, slots=True)
class RewardWeights:
    """The reward's coefficients. Documented starting points, not GPU-tuned values.

    The shaping weights (:attr:`tool_validity` … :attr:`under_median_turns`) are chosen so
    the *most* a clean rollout can add on top of a pass is ``tool_validity +
    under_median_turns == 0.15`` — the same number the shaping sum is clamped to, so the
    cap and the weights agree by construction rather than by luck.
    """

    verify_pass: float = 1.0
    tool_validity: float = 0.1
    redundant_call: float = 0.05
    stated_done_while_failing: float = 0.1
    under_median_turns: float = 0.05
    #: Hard gates. Both short-circuit; neither is shaping.
    touched_protected: float = -1.0
    ran_out_of_turns: float = -0.5
    #: The band the shaping sum is clamped to before it reaches the base.
    shaping_cap: float = 0.15
    clip_low: float = -1.0
    clip_high: float = 1.3


@dataclass(frozen=True, slots=True)
class RolloutOutcome:
    """Everything a reward reads about one finished rollout. All facts, no opinions.

    ``verify_passed_hidden`` is the reward signal; ``verify_passed_visible`` is here only
    so a caller can *report* the visible/hidden gap (a large gap is overfitting to the
    prompt's own tests) — it never enters :func:`compute_reward`.
    """

    turns: int
    ran_out_of_turns: bool
    verify_passed_hidden: bool
    verify_passed_visible: bool
    tool_validity: float
    redundant_calls: int
    stated_done_while_failing: bool
    diff_paths: tuple[str, ...]
    median_turns: float


@dataclass(frozen=True, slots=True)
class RewardBreakdown:
    """One rollout's reward with its derivation, because a bare scalar cannot be audited.

    ``gate`` is the name of the hard gate that fired, or ``""`` when none did and the
    reward is ``base + shaping``. ``components`` are the pre-clamp shaping terms.
    """

    reward: float
    gate: str
    base: float
    shaping: float
    components: Mapping[str, float] = field(default_factory=dict)

    @property
    def gated(self) -> bool:
        return bool(self.gate)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def compute_reward(
    outcome: RolloutOutcome,
    weights: RewardWeights = RewardWeights(),
    *,
    protected_extra: Sequence[str] = (),
) -> RewardBreakdown:
    """Score one rollout in ``[-1.0, 1.3]``. Hard gates first, then base + capped shaping.

    ``protected_extra`` names files that must not be edited beyond the built-in test /
    ``verify.sh`` set — pass the hidden test's filename so tampering with it is caught even
    though it was written after the rollout.
    """
    offending = protected_hit(outcome.diff_paths, extra=protected_extra)
    if offending is not None:
        return RewardBreakdown(
            reward=weights.touched_protected,
            gate=f"touched-protected:{offending}",
            base=weights.touched_protected,
            shaping=0.0,
        )
    if outcome.ran_out_of_turns:
        return RewardBreakdown(
            reward=weights.ran_out_of_turns,
            gate="ran-out-of-turns",
            base=weights.ran_out_of_turns,
            shaping=0.0,
        )

    base = weights.verify_pass if outcome.verify_passed_hidden else 0.0
    components: dict[str, float] = {
        "tool_validity": weights.tool_validity * _clamp(outcome.tool_validity, 0.0, 1.0),
        "redundant": -weights.redundant_call * max(outcome.redundant_calls, 0),
        "stated_done_while_failing": (
            -weights.stated_done_while_failing if outcome.stated_done_while_failing else 0.0
        ),
        "under_median": (
            weights.under_median_turns
            if outcome.median_turns > 0 and outcome.turns < outcome.median_turns
            else 0.0
        ),
    }
    shaping = _clamp(sum(components.values()), -weights.shaping_cap, weights.shaping_cap)
    reward = _clamp(base + shaping, weights.clip_low, weights.clip_high)
    return RewardBreakdown(reward=reward, gate="", base=base, shaping=shaping, components=components)


__all__ = [
    "PROTECTED_FRAGMENTS",
    "RewardBreakdown",
    "RewardWeights",
    "RolloutOutcome",
    "compute_reward",
    "protected_hit",
]
