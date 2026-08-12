"""Carrying a human's answer from a UI to the policy engine.

Before this module existed there was exactly one asker in ``src/``, ``UnattendedAsker``,
which always says no. That is the right default and it was also the *only* behaviour: a
person sitting at the real TUI could read an approval and had no way to grant it. These
tests cover the two paths that changed that, and — more importantly — the ways they must
still refuse.
"""

from __future__ import annotations

import pytest

from ronin.cli.approve import (
    NO_ANSWER,
    REASK,
    UNREADABLE,
    Handoff,
    PromptAsker,
    answer_for,
)
from ronin.core.types import ApprovalDecision, ApprovalRequest, DangerLevel
from ronin.safety.policy import Asker, Outcome

REQUEST = ApprovalRequest(
    tool_use_id="t1",
    name="Bash",
    danger_level=DangerLevel.DESTRUCTIVE,
    rendered="rm -rf ./build",
)
OTHER = ApprovalRequest(
    tool_use_id="t2",
    name="Bash",
    danger_level=DangerLevel.DESTRUCTIVE,
    rendered="rm -rf /",
)


# --------------------------------------------------------------------------- #
# the mapping
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("decision", "outcome"),
    [
        (ApprovalDecision(approved=True), Outcome.YES_ONCE),
        (ApprovalDecision(approved=True, remember=True), Outcome.YES_SESSION),
        (ApprovalDecision(approved=False, reason="no"), Outcome.NO),
    ],
)
def test_a_ui_decision_becomes_the_engine_s_answer(
    decision: ApprovalDecision, outcome: Outcome
) -> None:
    assert answer_for(decision).outcome is outcome


def test_remembering_is_session_scoped_not_written_to_disk() -> None:
    """``YES_PERSIST`` writes a standing rule into ``settings.local.json`` that applies
    on a day nobody remembers pressing a key. One keystroke should not buy that, so
    "always" means "for this session" — undone by closing the terminal. The engine tells
    the human which scope it applied, so nothing is hidden by the choice."""
    answer = answer_for(ApprovalDecision(approved=True, remember=True))
    assert answer.outcome is Outcome.YES_SESSION
    # Named rather than compared: mypy rejects `is not YES_PERSIST` on a value it has
    # narrowed to YES_SESSION, and the claim is about which outcome was chosen anyway.
    assert answer.outcome.value == "yes_session"
    assert answer.outcome.remembers, "it still remembers, just not forever"


def test_the_reason_survives_so_a_denial_is_actionable() -> None:
    answer = answer_for(ApprovalDecision(approved=False, reason="not that directory"))
    assert answer.feedback == "not that directory"


# --------------------------------------------------------------------------- #
# Handoff: the seam the policy asks through
# --------------------------------------------------------------------------- #


def answering(decision: ApprovalDecision) -> tuple[Handoff, list[ApprovalRequest]]:
    """A Handoff attached to a UI that answers with ``decision`` and records the ask."""
    seen: list[ApprovalRequest] = []
    handoff = Handoff()

    async def ui(request: ApprovalRequest) -> ApprovalDecision:
        seen.append(request)
        return decision

    handoff.attach(ui)
    return handoff, seen


async def test_the_question_reaches_the_ui_and_the_answer_comes_back() -> None:
    handoff, seen = answering(ApprovalDecision(approved=True))
    assert (await handoff.ask(REQUEST)).outcome is Outcome.YES_ONCE
    assert seen == [REQUEST], "the UI was asked about exactly this call"


async def test_the_ui_sees_the_request_it_is_answering() -> None:
    """Not a copy, not a re-derivation: the same object, carrying the same ``rendered``
    text the loop put in it. What is shown is what runs."""
    handoff, seen = answering(ApprovalDecision(approved=False))
    await handoff.ask(REQUEST)
    assert seen[0].rendered == "rm -rf ./build"
    assert seen[0] is REQUEST


async def test_with_no_ui_attached_the_answer_is_no() -> None:
    """The property that makes this safe to put in the approval path. A headless run, a
    nested subagent turn, a front end that never mounted — each arrives here as "nothing
    attached", and the answer is a refusal with words the model can act on."""
    handoff = Handoff()
    assert not handoff.attached
    answer = await handoff.ask(REQUEST)
    assert answer.outcome is Outcome.NO
    assert answer.feedback == NO_ANSWER


async def test_every_question_is_asked_rather_than_answered_from_memory() -> None:
    """No caching by ``tool_use_id`` or anything else. A repeated call is a repeated
    question; remembering an approval is the policy engine's job, and it has rules about
    which ones may be remembered at all."""
    asked = 0
    handoff = Handoff()

    async def ui(request: ApprovalRequest) -> ApprovalDecision:
        nonlocal asked
        asked += 1
        return ApprovalDecision(approved=True)

    handoff.attach(ui)
    await handoff.ask(REQUEST)
    await handoff.ask(REQUEST)
    assert asked == 2


def test_handoff_satisfies_the_asker_protocol() -> None:
    assert isinstance(Handoff(), Asker)


# --------------------------------------------------------------------------- #
# PromptAsker: the line session's answer path
# --------------------------------------------------------------------------- #


class Terminal:
    """A scripted line prompt. Answers come from a list; output is captured."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.written: list[str] = []
        self.asked: list[str] = []

    def prompt(self, question: str) -> str:
        self.asked.append(question)
        return self.answers.pop(0) if self.answers else ""

    def write(self, text: str) -> None:
        self.written.append(text)

    @property
    def output(self) -> str:
        return "".join(self.written)


@pytest.mark.parametrize(
    ("typed", "outcome"),
    [
        ("y", Outcome.YES_ONCE),
        ("yes", Outcome.YES_ONCE),
        ("Y", Outcome.YES_ONCE),
        ("a", Outcome.YES_SESSION),
        ("always", Outcome.YES_SESSION),
        ("n", Outcome.NO),
        ("no", Outcome.NO),
    ],
)
async def test_a_typed_answer_is_read_by_its_first_letter(typed: str, outcome: Outcome) -> None:
    """Whole words work because only the first character is consulted, and the case is
    folded — someone who types ``yes`` at a ``[y]es`` prompt meant yes."""
    terminal = Terminal(typed)
    assert (await PromptAsker(terminal.prompt, terminal.write).ask(REQUEST)).outcome is outcome


async def test_the_human_is_shown_what_they_are_deciding_on() -> None:
    """The same requirement the modal has: no approving text that the human never saw."""
    terminal = Terminal("y")
    await PromptAsker(terminal.prompt, terminal.write).ask(REQUEST)
    assert REQUEST.rendered in terminal.output
    assert "Bash" in terminal.output


async def test_an_unreadable_answer_is_asked_again_and_then_refused() -> None:
    """Bounded on purpose: the protocol says an asker must return, and one that keeps
    asking hangs the turn. Three tries covers a typo; a stuck pipe returning ``""``
    forever gets a refusal rather than a spin."""
    terminal = Terminal("what", "huh", "?")
    answer = await PromptAsker(terminal.prompt, terminal.write).ask(REQUEST)
    assert answer.outcome is Outcome.NO
    assert answer.feedback == UNREADABLE
    assert terminal.output.count(REASK) == 3
    assert len(terminal.asked) == 3


async def test_a_typo_before_a_real_answer_still_gets_the_real_answer() -> None:
    terminal = Terminal("k", "y")
    assert (await PromptAsker(terminal.prompt, terminal.write).ask(REQUEST)).outcome is (
        Outcome.YES_ONCE
    )


async def test_an_empty_line_is_not_an_approval() -> None:
    """A bare newline is what a held-down enter key produces, and what a closed stdin
    returns forever. Neither is consent."""
    terminal = Terminal("", "", "")
    assert (await PromptAsker(terminal.prompt, terminal.write).ask(REQUEST)).outcome is Outcome.NO


def test_prompt_asker_satisfies_the_asker_protocol() -> None:
    terminal = Terminal()
    assert isinstance(PromptAsker(terminal.prompt, terminal.write), Asker)
