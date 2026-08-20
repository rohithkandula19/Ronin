"""The verifiable reward: a completion's score is a fact, and the group math is GRPO's.

These tests pin the reward table's *ordering* (a correct call beats a well-formed
wrong-tool call beats silence beats a malformed call beats the v1 dialect) and the
group-relative advantage that turns those scalars into a training signal. No ML stack is
imported — that is the whole point of a verifier reward, and the test proves it stays
computable on a bare install.
"""

from __future__ import annotations

import json

import pytest
from ronin_training.adapter.config import DIALECT_CLOSE_TAG, DIALECT_OPEN_TAG
from ronin_training.adapter.reward import (
    DEFAULT_WEIGHTS,
    RewardWeights,
    group_advantages,
    make_reward_fn,
    score_turn,
)

# Small registry, same shape as the metric test's, so the two never disagree.
SCHEMAS = {
    "read_file": {
        "type": "object",
        "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}},
        "required": ["path"],
    },
    "glob": {
        "type": "object",
        "properties": {"pattern": {"type": "string"}},
        "required": ["pattern"],
    },
}


def _call(name: str, arguments: object) -> str:
    return (
        f"{DIALECT_OPEN_TAG}{json.dumps({'name': name, 'arguments': arguments})}{DIALECT_CLOSE_TAG}"
    )


# --------------------------------------------------------------------------- #
# score_turn
# --------------------------------------------------------------------------- #


def test_a_valid_call_to_the_expected_tool_earns_valid_plus_correct_tool() -> None:
    reward = score_turn(
        _call("read_file", {"path": "a.py"}), expect_tool="read_file", schemas=SCHEMAS
    )
    assert reward.valid is True
    assert reward.tool == "read_file"
    assert reward.value == DEFAULT_WEIGHTS.valid + DEFAULT_WEIGHTS.correct_tool
    assert reward.reason == "valid_call+correct_tool"
    assert reward.components == (
        ("valid", DEFAULT_WEIGHTS.valid),
        ("correct_tool", DEFAULT_WEIGHTS.correct_tool),
    )


def test_a_valid_call_to_the_wrong_tool_earns_valid_only() -> None:
    reward = score_turn(
        _call("glob", {"pattern": "*.py"}), expect_tool="read_file", schemas=SCHEMAS
    )
    assert reward.valid is True
    assert reward.tool == "glob"
    assert reward.value == DEFAULT_WEIGHTS.valid
    assert reward.reason == "valid_call"


def test_with_no_expected_tool_a_valid_call_is_rewarded_for_form_alone() -> None:
    reward = score_turn(_call("glob", {"pattern": "*.py"}), expect_tool=None, schemas=SCHEMAS)
    assert reward.value == DEFAULT_WEIGHTS.valid


def test_the_v1_dialect_is_the_one_outcome_pushed_below_zero() -> None:
    text = '<tool_call>{"name": "read_file", "arguments": {"path": "a"}}</tool_call>'
    reward = score_turn(text, expect_tool="read_file", schemas=SCHEMAS)
    assert reward.valid is False
    assert reward.reason == "v1_dialect"
    assert reward.value == DEFAULT_WEIGHTS.v1_dialect < 0.0


def test_an_attempted_but_malformed_call_earns_the_invalid_weight() -> None:
    text = f"{DIALECT_OPEN_TAG}{{not json{DIALECT_CLOSE_TAG}"
    reward = score_turn(text, expect_tool="read_file", schemas=SCHEMAS)
    assert reward.valid is False
    assert reward.reason.startswith("invalid_call:")
    assert reward.value == DEFAULT_WEIGHTS.invalid


def test_a_schema_violation_is_invalid_not_valid() -> None:
    # `path` is required; omitting it is a schema violation, not a parse error.
    reward = score_turn(_call("read_file", {"limit": 5}), expect_tool="read_file", schemas=SCHEMAS)
    assert reward.valid is False
    assert reward.value == DEFAULT_WEIGHTS.invalid


def test_no_tool_call_at_all_is_scored_no_attempt_not_invalid() -> None:
    reward = score_turn("I think the file is fine.", expect_tool="read_file", schemas=SCHEMAS)
    assert reward.valid is None
    assert reward.tool is None
    assert reward.reason == "no_attempt"
    assert reward.value == DEFAULT_WEIGHTS.no_attempt


def test_the_reward_ordering_is_the_policy_the_weights_encode() -> None:
    """correct > well-formed-wrong-tool > silent > malformed > v1-dialect."""
    correct = score_turn(
        _call("read_file", {"path": "a"}), expect_tool="read_file", schemas=SCHEMAS
    )
    wrong_tool = score_turn(
        _call("glob", {"pattern": "*"}), expect_tool="read_file", schemas=SCHEMAS
    )
    silent = score_turn("no call", expect_tool="read_file", schemas=SCHEMAS)
    malformed = score_turn(
        f"{DIALECT_OPEN_TAG}oops{DIALECT_CLOSE_TAG}", expect_tool="read_file", schemas=SCHEMAS
    )
    v1 = score_turn(
        '<tool_call>{"name":"read_file","arguments":{"path":"a"}}</tool_call>',
        expect_tool="read_file",
        schemas=SCHEMAS,
    )
    assert correct.value > wrong_tool.value >= silent.value >= malformed.value > v1.value


# --------------------------------------------------------------------------- #
# group_advantages
# --------------------------------------------------------------------------- #


def test_a_tied_group_has_zero_advantage_for_everyone() -> None:
    assert group_advantages([1.0, 1.0, 1.0]) == (0.0, 0.0, 0.0)


def test_advantages_are_the_group_relative_z_scores() -> None:
    adv = group_advantages([0.0, 2.0])
    # mean 1.0, pstdev 1.0 -> (-1, +1)
    assert adv == (-1.0, 1.0)


def test_a_group_of_one_has_no_relative_signal() -> None:
    assert group_advantages([3.0]) == (0.0,)


def test_an_empty_group_is_empty() -> None:
    assert group_advantages([]) == ()


def test_advantages_sum_to_zero_and_the_best_completion_is_positive() -> None:
    adv = group_advantages([0.0, 0.0, 1.5, 0.0])
    assert abs(sum(adv)) < 1e-9
    assert adv[2] == max(adv) > 0.0


# --------------------------------------------------------------------------- #
# make_reward_fn — the trl.GRPOTrainer-shaped callable
# --------------------------------------------------------------------------- #


def test_the_reward_fn_scores_a_batch_with_per_completion_expected_tools() -> None:
    fn = make_reward_fn(schemas=SCHEMAS)
    completions = [
        _call("read_file", {"path": "a"}),  # correct
        _call("glob", {"pattern": "*"}),  # valid, wrong tool
        "no call at all",  # silent
    ]
    values = fn(
        ["p1", "p2", "p3"],
        completions,
        expect_tool=["read_file", "read_file", "read_file"],
    )
    assert values == [
        DEFAULT_WEIGHTS.valid + DEFAULT_WEIGHTS.correct_tool,
        DEFAULT_WEIGHTS.valid,
        DEFAULT_WEIGHTS.no_attempt,
    ]


def test_the_reward_fn_scores_on_form_when_no_expected_tools_are_given() -> None:
    fn = make_reward_fn(schemas=SCHEMAS)
    values = fn(["p"], [_call("glob", {"pattern": "*"})])
    assert values == [DEFAULT_WEIGHTS.valid]


def test_a_mismatched_expected_tool_column_is_a_loud_error() -> None:
    fn = make_reward_fn(schemas=SCHEMAS)
    with pytest.raises(ValueError, match="one dataset column value per completion"):
        fn(["p1", "p2"], ["c1", "c2"], expect_tool=["read_file"])


def test_custom_weights_change_the_score_but_not_the_shape() -> None:
    weights = RewardWeights(valid=2.0, correct_tool=1.0, v1_dialect=-3.0)
    reward = score_turn(
        _call("read_file", {"path": "a"}), expect_tool="read_file", schemas=SCHEMAS, weights=weights
    )
    assert reward.value == 3.0
