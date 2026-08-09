"""The self-critique: tolerant parsing, biased toward one more iteration."""
from __future__ import annotations

import pytest

from ronin.verify.critique import (
    CRITIQUE_PROMPT,
    Critique,
    clamp_diff,
    critique_change,
    critique_gate,
    parse_critique,
)


@pytest.mark.parametrize(
    ("answer", "passes"),
    [
        ("yes", True),
        ("YES", True),
        ("yes\n", True),
        ("✅", True),
        ("Yes — every part of the request is addressed.", True),
        ("no", False),
        ("NO\nthe --json flag is missing", False),
        ("❌ the second endpoint was never added", False),
        ("nope, the migration is missing", False),
        ("Looking at the diff: no, the CLI flag is not implemented", False),
        ("yes, though I would also rename the helper", True),
    ],
)
def test_a_verdict_is_read_out_of_whatever_the_model_said(answer: str, passes: bool) -> None:
    assert parse_critique(answer).passes is passes


def test_the_missing_piece_is_kept_verbatim() -> None:
    critique = parse_critique("no\nthe --json flag the request asked for is not implemented")
    assert critique.passes is False
    assert critique.missing == "the --json flag the request asked for is not implemented"
    assert critique.parsed is True


def test_a_one_line_no_still_carries_something_to_act_on() -> None:
    critique = parse_critique("no, the second endpoint is not there")
    assert critique.passes is False
    assert "second endpoint" in critique.missing


@pytest.mark.parametrize("answer", ["", "   ", "I am not sure what to make of this diff."])
def test_an_unparseable_answer_fails_toward_one_more_iteration(answer: str) -> None:
    """The cheap error is an extra turn, not a false 'done'."""
    critique = parse_critique(answer)
    assert critique.passes is False
    assert critique.parsed is False
    assert critique.missing
    assert critique.raw == answer


def test_a_failing_critique_cannot_be_built_without_a_reason() -> None:
    with pytest.raises(ValueError, match="must say what is missing"):
        Critique(passes=False, missing="")


def test_the_diff_is_clamped_with_a_marker() -> None:
    clamped = clamp_diff("A" * 100 + "B" * 100, limit=50)
    assert "characters of diff cut from the middle" in clamped
    assert clamped.startswith("A")
    assert clamped.endswith("B")


async def test_the_prompt_carries_the_objective_and_the_diff() -> None:
    seen: list[str] = []

    async def ask(prompt: str) -> str:
        seen.append(prompt)
        return "yes"

    await critique_change(objective="add a --json flag", diff="+flag = True", ask=ask)

    assert "add a --json flag" in seen[0]
    assert "+flag = True" in seen[0]
    assert seen[0].startswith(CRITIQUE_PROMPT.split("\n", 1)[0])


async def test_a_long_diff_is_clamped_before_it_reaches_the_fast_model() -> None:
    seen: list[str] = []

    async def ask(prompt: str) -> str:
        seen.append(prompt)
        return "yes"

    await critique_change(objective="x", diff="d" * 5_000, ask=ask, limit=200)

    assert len(seen[0]) < 1_500
    assert "characters of diff cut" in seen[0]


async def test_a_model_failure_is_not_a_pass() -> None:
    async def ask(prompt: str) -> str:
        del prompt
        raise TimeoutError("fast model unavailable")

    critique = await critique_change(objective="x", diff="y", ask=ask)

    assert critique.passes is False
    assert critique.parsed is False
    assert "could not run" in critique.missing


async def test_a_passing_critique_does_not_buy_an_iteration() -> None:
    iterations: list[str] = []

    async def ask(prompt: str) -> str:
        del prompt
        return "yes"

    async def iterate(missing: str) -> str:
        iterations.append(missing)
        return "unused"

    gate = await critique_gate(objective="x", diff="y", ask=ask, iterate=iterate)

    assert gate.passes
    assert gate.iterated is False
    assert gate.rounds == (Critique(passes=True, missing="", raw="yes"),)
    assert iterations == []


async def test_a_no_buys_exactly_one_iteration_and_then_passes() -> None:
    answers = ["no\nthe --json flag is missing", "yes"]
    prompts: list[str] = []
    iterations: list[str] = []

    async def ask(prompt: str) -> str:
        prompts.append(prompt)
        return answers[min(len(prompts) - 1, len(answers) - 1)]

    async def iterate(missing: str) -> str:
        iterations.append(missing)
        return "+ --json added"

    gate = await critique_gate(objective="x", diff="y", ask=ask, iterate=iterate)

    assert gate.passes
    assert gate.iterated
    assert len(gate.rounds) == 2
    assert iterations == ["the --json flag is missing"]
    assert len(prompts) == 2
    assert "+ --json added" in prompts[1]
    assert gate.capped is False


async def test_a_second_no_is_capped_and_says_so() -> None:
    async def ask(prompt: str) -> str:
        del prompt
        return "no\nstill missing the flag"

    async def iterate(missing: str) -> str:
        del missing
        return "+ nothing useful"

    gate = await critique_gate(objective="x", diff="y", ask=ask, iterate=iterate)

    assert gate.passes is False
    assert gate.capped
    assert len(gate.rounds) == 2
    rendered = gate.render()
    assert "critique 1: no" in rendered
    assert "critique 2: no" in rendered
    assert "the cap is reached" in rendered


async def test_the_gate_never_calls_the_model_more_than_twice() -> None:
    calls = {"n": 0}

    async def ask(prompt: str) -> str:
        del prompt
        calls["n"] += 1
        return "no\nmissing"

    async def iterate(missing: str) -> str:
        del missing
        return "diff"

    await critique_gate(objective="x", diff="y", ask=ask, iterate=iterate)

    assert calls["n"] == 2


def test_the_prompt_is_readable_and_asks_for_nothing_else() -> None:
    """The prompt is a module constant so a reviewer can audit it here."""
    assert "does this change do what was asked" in CRITIQUE_PROMPT
    assert "Do not review style" in CRITIQUE_PROMPT
    assert "{objective}" in CRITIQUE_PROMPT
    assert "{diff}" in CRITIQUE_PROMPT
