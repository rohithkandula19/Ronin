"""The verifiable reward — the ``V`` in RLVR, and the whole reason GRPO can run here offline.

GRPO (the RF.3 pass) needs a reward for each sampled completion. The expensive way to
get one is a learned reward model; the honest way, for a model whose entire thesis is
*protocol compliance*, is a **verifier** — a deterministic function that reads the
completion and scores it against facts, not against another network's opinion. That is
what makes the signal reproducible, auditable, and computable on a machine with no GPU:
the reward for ``<ronin:tool_call>\\n{"name": "read", ...}`` is not a matter of taste.

Every component here is already defined in :mod:`ronin_training.adapter.metrics`, and is
reused rather than reimplemented, for the same reason the metric reuses the runtime's
fingerprint: a reward that grades tool syntax differently from how the eval measures it
would train the model toward a target the report does not credit. So the reward *is* the
metric, turned from a fraction into a scalar:

* a call that parses and validates against the registry schema earns :attr:`RewardWeights.valid`;
* the **v1** ``<tool_call>`` dialect — a call the v2 runtime will never execute — is the
  one outcome punished below zero (:attr:`RewardWeights.v1_dialect`), because it is the
  failure that scored 0/4 and the one the adapter exists to erase;
* an attempted-but-malformed call earns :attr:`RewardWeights.invalid` (zero by default:
  trying and mis-serialising is not worse than staying silent, it is just not rewarded);
* naming the *expected* tool, when the prompt has one, earns :attr:`RewardWeights.correct_tool`
  on top — so the model is pulled toward the right action, not merely a well-formed one.

The weights are defaults, not tuned values: no GPU here ever ran GRPO to sweep them, and
a number presented as tuned that was in fact guessed is the exact dishonesty this package
refuses elsewhere. They are documented as a starting point and are config-overridable.

Nothing in this module imports ``torch``, ``trl`` or ``mlx``. :func:`make_reward_fn`
returns a plain callable with the signature ``trl.GRPOTrainer`` expects of a reward
function, so a GPU run wires it in with one line, but the callable itself is pure Python
over :mod:`.metrics` and is unit-tested without an ML stack present.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from statistics import fmean, pstdev
from typing import Any

from .config import DIALECT_OPEN_TAG, V1_DIALECT_OPEN_TAG
from .metrics import CallCheck, attempted_call, check_raw_call, find_call_blocks

# --------------------------------------------------------------------------- #
# The weights
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class RewardWeights:
    """What each verifiable outcome is worth. Defaults, not tuned constants.

    The scale is deliberately small and centred near zero: GRPO normalises rewards
    within each group (see :func:`group_advantages`), so only the *ordering* and the
    *relative gaps* between these numbers matter, never their absolute size. The gaps
    encode a policy — a correct call beats a well-formed wrong-tool call beats a silent
    turn beats a malformed call beats the wrong dialect — and that policy is the claim a
    reviewer should argue with, so it is one readable table rather than magic numbers
    sprinkled through the scorer.
    """

    #: A call that parses and validates against the tool's registry schema.
    valid: float = 1.0
    #: On top of ``valid``, for naming the tool the prompt expected (when it names one).
    correct_tool: float = 0.5
    #: Attempted a v2 call but it did not parse/validate. Zero, not negative: a model
    #: that tries and mis-serialises is on the right track and GRPO will still rank it
    #: below a valid sibling in the same group.
    invalid: float = 0.0
    #: Emitted no tool call at all. Also zero by default; raise its magnitude (negative)
    #: only for a prompt set where every prompt genuinely demands a call.
    no_attempt: float = 0.0
    #: The v1 ``<tool_call>`` dialect. The one sub-zero outcome: it is a call the v2
    #: runtime cannot execute, and it is the specific regression this adapter erases.
    v1_dialect: float = -1.0


DEFAULT_WEIGHTS = RewardWeights()


# --------------------------------------------------------------------------- #
# One completion's reward, with its reasoning kept
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class TurnReward:
    """The scalar a completion earned, and the verifiable facts that produced it.

    The breakdown is carried for the same reason the metrics carry their denominators:
    a reward with no visible derivation is indistinguishable from a fabricated one, and
    a GRPO run that later looks suspect must be auditable turn by turn.
    """

    value: float
    #: The tool the completion actually called, or ``None`` if it called none.
    tool: str | None
    #: Whether the (first) call parsed and validated. ``None`` when none was attempted.
    valid: bool | None
    #: A short, stable label for why this reward and not another.
    reason: str
    #: ``(component, contribution)`` pairs summing to ``value``, in application order.
    components: tuple[tuple[str, float], ...] = ()


def _called_tool(text: str) -> str | None:
    """The name of the first well-formed v2 call block, or ``None``.

    Parsed here (not returned by :func:`check_raw_call`, which yields only ok/reason)
    solely to compare against the expected tool. Uses the same public block finder as
    the metric, so the two never disagree about which block is "first".
    """
    blocks = find_call_blocks(text)
    if not blocks:
        return None
    try:
        parsed = json.loads(blocks[0])
    except ValueError:
        return None
    if isinstance(parsed, Mapping):
        name = parsed.get("name")
        if isinstance(name, str) and name:
            return name
    return None


def score_turn(
    text: str,
    *,
    expect_tool: str | None,
    schemas: Mapping[str, Mapping[str, Any]],
    weights: RewardWeights = DEFAULT_WEIGHTS,
) -> TurnReward:
    """Score one assistant completion. Pure: no I/O, no randomness, no ML stack.

    ``expect_tool`` is the tool a passing trajectory took at this point, when one is
    known; pass ``None`` to reward well-formedness alone (useful for prompts with more
    than one correct action). ``schemas`` is the real v2 registry — the output of
    :func:`ronin_training.adapter.metrics.load_tool_schemas` — so "valid" means the
    runtime would actually accept the call.
    """
    if not attempted_call(text):
        return TurnReward(
            value=weights.no_attempt,
            tool=None,
            valid=None,
            reason="no_attempt",
            components=(("no_attempt", weights.no_attempt),),
        )
    # Mirror check_raw_call's own highest-severity branch without reparsing: only the v1
    # tag present means the wrong dialect entirely.
    if DIALECT_OPEN_TAG not in text and V1_DIALECT_OPEN_TAG in text:
        return TurnReward(
            value=weights.v1_dialect,
            tool=None,
            valid=False,
            reason="v1_dialect",
            components=(("v1_dialect", weights.v1_dialect),),
        )
    check: CallCheck = check_raw_call(text, schemas=schemas)
    if not check.ok:
        return TurnReward(
            value=weights.invalid,
            tool=_called_tool(text),
            valid=False,
            reason=f"invalid_call: {check.reason}",
            components=(("invalid", weights.invalid),),
        )
    tool = _called_tool(text)
    components: list[tuple[str, float]] = [("valid", weights.valid)]
    reason = "valid_call"
    if expect_tool is not None and tool == expect_tool:
        components.append(("correct_tool", weights.correct_tool))
        reason = "valid_call+correct_tool"
    total = sum(contribution for _, contribution in components)
    return TurnReward(
        value=total, tool=tool, valid=True, reason=reason, components=tuple(components)
    )


# --------------------------------------------------------------------------- #
# GRPO's group-relative advantage
# --------------------------------------------------------------------------- #


def group_advantages(rewards: Sequence[float]) -> tuple[float, ...]:
    """Normalise a group of rewards to advantages the way GRPO does.

    GRPO's one idea is that it needs no value network: it samples ``G`` completions for
    one prompt, and each completion's advantage is how far its reward sits from the
    group's own mean, in units of the group's own spread — ``(r_i - mean) / std``. A
    group whose completions all earned the same reward has zero spread and therefore
    zero advantage for every member: there is nothing to learn from a tie, and dividing
    by that zero would be the bug, so it returns zeros explicitly.

    Population standard deviation (``pstdev``) is used, not sample: the group *is* the
    population being compared, and there is no wider distribution being estimated from a
    sample of it. This matches trl's ``GRPOTrainer`` default (mean/std over the group).
    """
    if not rewards:
        return ()
    if len(rewards) == 1:
        # A group of one has no relative signal — its only sibling is itself.
        return (0.0,)
    spread = pstdev(rewards)
    if spread == 0.0:
        return tuple(0.0 for _ in rewards)
    mean = fmean(rewards)
    return tuple((r - mean) / spread for r in rewards)


# --------------------------------------------------------------------------- #
# The callable a GPU run hands to trl.GRPOTrainer
# --------------------------------------------------------------------------- #

#: The signature ``trl.GRPOTrainer`` calls a reward function with: a list of prompts, a
#: matching list of generated completions, and arbitrary extra dataset columns as
#: keyword lists (e.g. ``expect_tool=[...]``). It must return one float per completion.
RewardFn = Callable[..., list[float]]


def make_reward_fn(
    *,
    schemas: Mapping[str, Mapping[str, Any]],
    weights: RewardWeights = DEFAULT_WEIGHTS,
) -> RewardFn:
    """Build the reward function ``trl.GRPOTrainer(reward_funcs=[...])`` expects.

    Returned as a closure over the schemas and weights so the trainer calls it with
    just ``(prompts, completions)`` plus whatever extra columns the dataset carries. The
    ``expect_tool`` column is optional; absent, every completion is scored on
    well-formedness alone. trl is never imported — this is a plain callable, and
    :func:`score_turn` under it is what the tests exercise.
    """

    def reward_fn(
        prompts: Sequence[str],
        completions: Sequence[str],
        *,
        expect_tool: Sequence[str | None] | None = None,
        **_: object,
    ) -> list[float]:
        expected: Sequence[str | None]
        expected = expect_tool if expect_tool is not None else [None] * len(completions)
        if len(expected) != len(completions):
            raise ValueError(
                f"expect_tool has {len(expected)} entries for {len(completions)} "
                "completions — trl passes one dataset column value per completion"
            )
        return [
            score_turn(completion, expect_tool=want, schemas=schemas, weights=weights).value
            for completion, want in zip(completions, expected, strict=True)
        ]

    return reward_fn


__all__ = [
    "DEFAULT_WEIGHTS",
    "RewardFn",
    "RewardWeights",
    "TurnReward",
    "group_advantages",
    "make_reward_fn",
    "score_turn",
]
