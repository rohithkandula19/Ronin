"""A mistyped verb used to be a request, and requests cost money.

Bare positional prompts are deliberate: `ronin fix the failing test` is the shape
people want, and it is why `ronin sesions` was not a usage error. It was a prompt.
The model was billed to think about the word "sesions", and under `auto_edit` it
could answer with an edit and a shell command — a typo starting work.

The fix has to be narrow or it is worse than the bug, because the thing it must not
do is second-guess a real prompt. So these tests are mostly the *negative* direction:
one-word prompts, prompts whose first word resembles a verb, and every genuine verb
still reaching its command. The refusal only fires on a short line whose first word
is close enough to a verb to be a slip of the fingers, and it always names the escape
hatch.
"""

from __future__ import annotations

import pytest

from ronin.cli.main import SUBCOMMANDS, TYPO_MAX_WORDS, Command, Options, Usage, parse


def refusal(argv: list[str]) -> str:
    parsed = parse(argv)
    assert isinstance(parsed, Usage), f"{argv} was accepted: {parsed}"
    return parsed.message


def accepted(argv: list[str]) -> Options:
    parsed = parse(argv)
    assert isinstance(parsed, Options), f"{argv} was refused: {parsed}"
    return parsed


# --------------------------------------------------------------------------- #
# the typo
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("typed", "meant"),
    [
        ("sesions", "sessions"),
        ("sessons", "sessions"),
        ("doctro", "doctor"),
        ("docter", "doctor"),
        ("exprot", "export"),
        ("telemtry", "telemetry"),
        ("mcp-serv", "mcp-serve"),
    ],
)
def test_a_mistyped_verb_is_refused_and_the_right_one_named(typed: str, meant: str) -> None:
    message = refusal([typed])
    assert f"{typed!r} is not a command" in message
    assert f"did you mean {meant!r}" in message


def test_a_mistyped_verb_with_its_argument_is_refused_too() -> None:
    assert "did you mean 'telemetry'" in refusal(["telemtry", "on"])


def test_the_refusal_says_how_to_mean_it_anyway() -> None:
    """A refusal with no way past it is one people work around by not using the tool."""
    message = refusal(["sesions"])
    assert "-p 'sesions'" in message
    assert "typo should not start work" in message


def test_the_escape_hatch_actually_works() -> None:
    assert accepted(["-p", "sesions"]).prompt == "sesions"


def test_nothing_is_refused_once_print_was_asked_for() -> None:
    """`--print` is an explicit statement that the words are the prompt."""
    assert accepted(["-p", "doctro"]).prompt == "doctro"


# --------------------------------------------------------------------------- #
# and a real prompt is never touched
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "prompt",
    [
        "fix the test",
        "why is this slow",
        "refactor the parser",
        "run the tests",
        "explain this",
        "please",
        "fix",
        "test",
        "build",
        "deploy",
        "help me understand the compaction policy",
    ],
)
def test_a_real_prompt_runs(prompt: str) -> None:
    parsed = accepted(prompt.split())
    assert parsed.command is Command.RUN
    assert parsed.prompt == prompt


def test_a_long_line_is_taken_at_face_value(prompt: str = "export the sessions to html") -> None:
    """Past the word cap nothing is examined, however much the first word resembles a verb.

    `export` here is the *literal* verb and would be caught as a subcommand anyway, so
    the case that matters is the one below: a near-miss buried in a real sentence.
    """
    assert accepted(prompt.split()).command is Command.EXPORT


def test_a_near_miss_inside_a_real_sentence_is_left_alone() -> None:
    words = ["sesions", "are", "not", "being", "saved", "properly"]
    assert len(words) > TYPO_MAX_WORDS
    assert accepted(words).prompt == " ".join(words)


@pytest.mark.parametrize("verb", sorted(SUBCOMMANDS))
def test_every_real_verb_still_reaches_its_command(verb: str) -> None:
    """The refusal must never stand between a user and a verb that exists."""
    parsed = parse([verb])
    # Some verbs need an argument and say so; none of them may be mistaken for a typo.
    message = parsed.message if isinstance(parsed, Usage) else ""
    assert "did you mean" not in message


def test_a_word_that_resembles_nothing_is_a_prompt() -> None:
    assert accepted(["zzzzqqq"]).prompt == "zzzzqqq"


def test_an_empty_command_line_is_not_a_typo() -> None:
    assert accepted([]).prompt == ""


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__])
