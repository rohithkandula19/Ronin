"""The DPO behavioural eval hooks: recovery (reused from metrics) and loop rate (added)."""

from __future__ import annotations

import pytest
from ronin_training.adapter.metrics import VALID_CALL, Setback, TurnRecord
from ronin_training.dpo.eval_hook import (
    LOOP_THRESHOLD,
    LoopRate,
    loop_rate,
    recovery_rate,
    rollout_looped,
)


def test_loop_rate_counts_rollouts_that_repeat_an_action() -> None:
    rollouts = [
        ["grep:x", "grep:x", "grep:x"],  # looped (3 identical)
        ["read:a", "edit:a", "bash:test"],  # healthy
        ["grep:y", "grep:y", "read:y"],  # only twice — not a loop at threshold 3
    ]
    rate = loop_rate(rollouts)
    assert (rate.looped, rate.total) == (1, 3)
    assert rate.rate is not None


def test_rollout_looped_uses_the_stall_threshold() -> None:
    assert LOOP_THRESHOLD == 3
    assert rollout_looped(["a", "a", "a"]) is True
    assert rollout_looped(["a", "a", "b"]) is False
    assert rollout_looped([]) is False


def test_an_empty_holdout_has_no_loop_rate_rather_than_zero() -> None:
    assert loop_rate([]).rate is None
    assert LoopRate(looped=0, total=0).rate is None


def test_recovery_rate_is_the_metrics_definition_reused() -> None:
    # A setback followed by a *different* action recovers; re-issuing the very
    # fingerprint that just failed does not. This is metrics.recovery_rate, imported
    # through the DPO hook unchanged, so the DPO gate and the three-way eval agree.
    turns = [
        # setback on "read:a", answered with a new action -> recovered.
        TurnRecord(
            task_id="t",
            setback=Setback.FAILED_RESULT,
            setback_fingerprint="read:a",
            call=VALID_CALL,
            fingerprint="grep:a",
        ),
        # setback on "grep:b", answered by re-issuing "grep:b" -> did not recover.
        TurnRecord(
            task_id="t",
            setback=Setback.FAILED_RESULT,
            setback_fingerprint="grep:b",
            call=VALID_CALL,
            fingerprint="grep:b",
        ),
    ]
    score = recovery_rate(turns)
    assert (score.recovered, score.opportunities) == (1, 2)
    assert score.rate == pytest.approx(0.5)
