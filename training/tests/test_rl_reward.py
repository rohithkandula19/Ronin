"""The RLVR reward — every hard gate, the hidden-test rule, the shaping cap, the clip."""

from __future__ import annotations

from ronin_training.rl.reward import (
    RewardWeights,
    RolloutOutcome,
    compute_reward,
    protected_hit,
)


def _outcome(**overrides: object) -> RolloutOutcome:
    base: dict[str, object] = {
        "turns": 4,
        "ran_out_of_turns": False,
        "verify_passed_hidden": True,
        "verify_passed_visible": True,
        "tool_validity": 1.0,
        "redundant_calls": 0,
        "stated_done_while_failing": False,
        "diff_paths": ("src/mod.py",),
        "median_turns": 6.0,
    }
    base.update(overrides)
    return RolloutOutcome(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Hard gates — first, short-circuiting, worse than any honest run
# --------------------------------------------------------------------------- #


def test_editing_a_test_file_is_gated_to_minus_one_even_if_everything_passes() -> None:
    r = compute_reward(_outcome(diff_paths=("src/mod.py", "tests/test_mod.py")))
    assert r.reward == -1.0
    assert r.gate.startswith("touched-protected")
    assert r.shaping == 0.0  # nothing else is computed


def test_editing_the_verifier_is_gated() -> None:
    assert compute_reward(_outcome(diff_paths=("verify.sh",))).reward == -1.0


def test_a_test_prefixed_file_anywhere_is_gated() -> None:
    assert compute_reward(_outcome(diff_paths=("pkg/test_thing.py",))).reward == -1.0
    assert compute_reward(_outcome(diff_paths=("pkg/thing_test.py",))).reward == -1.0
    assert compute_reward(_outcome(diff_paths=("conftest.py",))).reward == -1.0


def test_the_cheat_gate_beats_a_passing_run() -> None:
    # A rollout that passed the hidden suite but got there by touching tests still scores
    # -1.0: a gamed pass can never out-earn honest work.
    r = compute_reward(_outcome(verify_passed_hidden=True, diff_paths=("tests/test_x.py",)))
    assert r.reward == -1.0


def test_running_out_of_turns_is_minus_half() -> None:
    r = compute_reward(_outcome(ran_out_of_turns=True, verify_passed_hidden=False))
    assert r.reward == -0.5
    assert r.gate == "ran-out-of-turns"


def test_the_cheat_gate_precedes_the_turn_gate() -> None:
    r = compute_reward(_outcome(ran_out_of_turns=True, diff_paths=("tests/test_x.py",)))
    assert r.reward == -1.0  # touched-protected wins over ran-out-of-turns


# --------------------------------------------------------------------------- #
# The hidden-test rule — the pass signal is the HIDDEN suite, never the visible one
# --------------------------------------------------------------------------- #


def test_the_base_reward_follows_the_hidden_result_not_the_visible_one() -> None:
    # Passed the tests it could see, failed the ones it could not: no +1.0.
    overfit = compute_reward(_outcome(verify_passed_visible=True, verify_passed_hidden=False))
    assert overfit.base == 0.0
    assert overfit.reward < 1.0

    # Failed the visible suite mid-run but the hidden suite passed: the +1.0 is real.
    generalised = compute_reward(_outcome(verify_passed_visible=False, verify_passed_hidden=True))
    assert generalised.base == 1.0
    assert generalised.reward >= 1.0


def test_a_hidden_pass_earns_the_task_reward() -> None:
    assert compute_reward(_outcome(verify_passed_hidden=True)).base == 1.0
    assert compute_reward(_outcome(verify_passed_hidden=False)).base == 0.0


# --------------------------------------------------------------------------- #
# Shaping — capped below the task signal so it cannot become the objective
# --------------------------------------------------------------------------- #


def test_total_shaping_never_exceeds_the_cap_however_extreme_the_inputs() -> None:
    weights = RewardWeights()
    # A pile of redundant calls would be -0.5 unclamped; the cap holds it to -0.15.
    hammered = compute_reward(
        _outcome(verify_passed_hidden=False, redundant_calls=100, stated_done_while_failing=True)
    )
    assert hammered.shaping >= -weights.shaping_cap
    # Best-case shaping (perfect tools, under median) is exactly the cap, not more.
    best = compute_reward(_outcome(verify_passed_hidden=False, tool_validity=1.0, turns=1))
    assert best.shaping <= weights.shaping_cap
    assert abs(best.shaping) <= 0.15


def test_the_under_median_bonus_and_over_median_absence() -> None:
    under = compute_reward(_outcome(verify_passed_hidden=True, turns=2, median_turns=6.0))
    over = compute_reward(_outcome(verify_passed_hidden=True, turns=9, median_turns=6.0))
    assert under.components["under_median"] > 0.0
    assert over.components["under_median"] == 0.0
    assert under.reward > over.reward


def test_stated_done_while_failing_is_penalised() -> None:
    r = compute_reward(_outcome(verify_passed_hidden=False, stated_done_while_failing=True))
    assert r.components["stated_done_while_failing"] < 0.0


def test_tool_validity_is_clamped_to_zero_one() -> None:
    # A bogus >1 validity cannot buy more than the weight allows.
    r = compute_reward(_outcome(verify_passed_hidden=False, tool_validity=5.0))
    assert r.components["tool_validity"] <= RewardWeights().tool_validity


# --------------------------------------------------------------------------- #
# The clip, and the protected-path helper
# --------------------------------------------------------------------------- #


def test_the_reward_is_clipped_to_the_band() -> None:
    weights = RewardWeights()
    for _ in range(1):
        r = compute_reward(_outcome(verify_passed_hidden=True, tool_validity=1.0, turns=1))
        assert weights.clip_low <= r.reward <= weights.clip_high


def test_protected_hit_names_the_first_offending_path() -> None:
    assert protected_hit(("a.py", "tests/test_b.py", "c.py")) == "tests/test_b.py"
    assert protected_hit(("a.py", "b.py")) is None
    assert protected_hit(("out.py",), extra=("out.py",)) == "out.py"
