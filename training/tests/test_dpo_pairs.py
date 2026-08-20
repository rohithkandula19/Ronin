"""The preference-pair builder — prefix-identical, only-the-assistant-differs, balanced.

The load-bearing test: a built pair shares its prompt and differs *only* in the assistant
turn. If that holds, DPO trains on the behaviour, not on a prefix artefact.
"""

from __future__ import annotations

from typing import Any

import pytest
from ronin_training.dpo.pairs import (
    Pair,
    PairError,
    PreferenceClass,
    balance,
    class_counts,
    correct_turn,
    pair_from_rejection,
)

_PROMPT: list[dict[str, Any]] = [
    {"role": "system", "content": "you are ronin"},
    {"role": "user", "content": "make the failing test pass"},
]


def _rejected(pref: PreferenceClass) -> dict[str, Any]:
    # A plausible failing assistant turn per class.
    if pref is PreferenceClass.MALFORMED_JSON:
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "read", "arguments": '{"path": "a"}'}}],
        }
    if pref is PreferenceClass.PREMATURE_DONE:
        return {"role": "assistant", "content": "done — the fix looks right"}
    if pref is PreferenceClass.IGNORED_DENIAL:
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "bash", "arguments": {"command": "rm -rf /"}}}],
        }
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"function": {"name": "grep", "arguments": {"pattern": "x"}}}],
    }


def test_a_built_pair_shares_the_prompt_and_differs_only_in_the_assistant_turn() -> None:
    pair = pair_from_rejection(
        _PROMPT, _rejected(PreferenceClass.BLIND_EDIT), PreferenceClass.BLIND_EDIT
    )
    row = pair.to_row()
    # Prefix identity: the prompt is one shared list; chosen/rejected are single turns.
    assert row["prompt"] == _PROMPT
    assert len(row["chosen"]) == 1 and len(row["rejected"]) == 1
    assert row["chosen"][0]["role"] == "assistant" and row["rejected"][0]["role"] == "assistant"
    assert row["chosen"] != row["rejected"], "only the assistant turn differs, and it must"


def test_every_class_synthesizes_a_different_chosen_turn() -> None:
    for pref in PreferenceClass:
        rejected = _rejected(pref)
        chosen = correct_turn(pref, rejected)
        assert chosen["role"] == "assistant"
        assert chosen != rejected, f"{pref}: synthesized chosen equals rejected"


def test_a_pair_with_a_differing_prompt_cannot_be_constructed() -> None:
    # There is no way to build a Pair whose two sides carry different prefixes: the prompt is
    # a single shared field. A same rejected==chosen pair is refused outright.
    same = {"role": "assistant", "content": "identical"}
    with pytest.raises(PairError, match="identical"):
        Pair(tuple(_PROMPT), same, dict(same), PreferenceClass.BLIND_EDIT)


def test_a_non_assistant_side_is_refused() -> None:
    with pytest.raises(PairError, match="assistant turn"):
        Pair(
            tuple(_PROMPT),
            {"role": "user", "content": "x"},
            {"role": "assistant", "content": "y"},
            PreferenceClass.BLIND_EDIT,
        )


def test_an_empty_prompt_is_refused() -> None:
    with pytest.raises(PairError, match="shared prompt"):
        Pair(
            (),
            {"role": "assistant", "content": "a"},
            {"role": "assistant", "content": "b"},
            PreferenceClass.BLIND_EDIT,
        )


def test_a_donor_chosen_is_not_flagged_synthesized() -> None:
    donor = {"role": "assistant", "content": "the real corrected turn from a passing run"}
    pair = pair_from_rejection(
        _PROMPT, _rejected(PreferenceClass.BLIND_EDIT), PreferenceClass.BLIND_EDIT, chosen=donor
    )
    assert pair.synthesized is False
    assert "synthesized_chosen" not in pair.to_row()
    # A synthesized one is flagged, so a later analysis can down-weight it.
    synth = pair_from_rejection(
        _PROMPT, _rejected(PreferenceClass.BLIND_EDIT), PreferenceClass.BLIND_EDIT
    )
    assert synth.synthesized is True and synth.to_row()["synthesized_chosen"] is True


def test_balance_caps_every_class_to_the_same_count() -> None:
    pairs: list[Pair] = []
    # 5 blind-edit, 2 looping, 3 premature-done -> balance to 2 each.
    for _ in range(5):
        pairs.append(
            pair_from_rejection(
                _PROMPT, _rejected(PreferenceClass.BLIND_EDIT), PreferenceClass.BLIND_EDIT
            )
        )
    for _ in range(2):
        pairs.append(
            pair_from_rejection(
                _PROMPT, _rejected(PreferenceClass.REPEATED_ACTION), PreferenceClass.REPEATED_ACTION
            )
        )
    for _ in range(3):
        pairs.append(
            pair_from_rejection(
                _PROMPT, _rejected(PreferenceClass.PREMATURE_DONE), PreferenceClass.PREMATURE_DONE
            )
        )

    balanced = balance(pairs, seed=0)
    counts = class_counts(balanced)
    present = {k: v for k, v in counts.items() if v}
    assert set(present.values()) == {2}, f"every present class capped to the min (2): {present}"
    assert len(present) == 3


def test_balance_is_deterministic_for_a_seed() -> None:
    pairs = [
        pair_from_rejection(
            _PROMPT, {"role": "assistant", "content": f"try {i}"}, PreferenceClass.BLIND_EDIT
        )
        for i in range(6)
    ] + [
        pair_from_rejection(
            _PROMPT, _rejected(PreferenceClass.REPEATED_ACTION), PreferenceClass.REPEATED_ACTION
        )
    ]
    a = [p.rejected for p in balance(pairs, seed=7)]
    b = [p.rejected for p in balance(pairs, seed=7)]
    assert a == b
