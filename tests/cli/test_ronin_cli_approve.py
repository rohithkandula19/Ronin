"""Carrying a human's answer from a UI to the policy engine.

Before this module existed there was exactly one asker in ``src/``, ``UnattendedAsker``,
which always says no. That is the right default and it was also the *only* behaviour: a
person sitting at the real TUI could read an approval and had no way to grant it. These
tests cover the two paths that changed that, and — more importantly — the ways they must
still refuse.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ronin.cli.approve import (
    NO_ANSWER,
    REASK,
    UNREADABLE,
    Handoff,
    PromptAsker,
    answer_for,
)
from ronin.core.types import ApprovalDecision, ApprovalRequest, DangerLevel, ToolSpec, ToolUse
from ronin.safety.denylist import Denylist
from ronin.safety.policy import (
    Answer,
    Asker,
    Outcome,
    PolicyEngine,
    RuleSet,
    UnattendedAsker,
    builtin_rules,
)
from ronin.ui.reduce import decision_for, deny_with

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


async def test_a_typed_reason_reaches_the_model_verbatim_and_first() -> None:
    """The whole chain, end to end: keystrokes in, model-readable correction out.

    ``deny_with`` is what the modal's reason line produces. This drives it through
    ``answer_for`` into a real ``PolicyEngine`` and asserts the sentence survives into
    the text the *model* is handed — not paraphrased, not prefixed away, and ahead of
    the engine's own framing so the model reads the correction before the boilerplate.

    Asserted here rather than only in the UI because the UI can only prove it built the
    decision; whether the words reach the model is a property of three layers agreeing.
    """
    correction = "use the staging database, not production"

    class Reasoning:
        async def ask(self, request: ApprovalRequest) -> Answer:
            del request
            return answer_for(deny_with(correction))

    policy = PolicyEngine(
        rules=RuleSet(rules=builtin_rules()),
        asker=Reasoning(),
        denylist=Denylist(workspace_root=Path("/work"), home=Path("/home/dev")),
    )
    spec = ToolSpec(
        name="bash",
        description="Run a command.",
        danger_level=DangerLevel.DESTRUCTIVE,
        requires_approval=True,
    )
    use = ToolUse(id="c1", name="bash", arguments={"command": "./deploy.sh"})

    decision = await policy.approve(spec, use, rendered="./deploy.sh")

    assert decision.approved is False
    assert correction in decision.reason, "the human's words did not reach the model"
    # First: the model reads the correction before the engine's framing around it.
    assert decision.reason.index(correction) < decision.reason.index("correction, not a")
    assert "declined" in decision.reason


async def test_an_empty_typed_reason_does_not_send_the_model_a_blank_correction() -> None:
    """`deny_with("")` must degrade to the engine's no-reason wording.

    Otherwise the model receives "the user declined and said:" with nothing after the
    colon, which reads as a malfunction — and a model that reads a refusal as a bug
    retries the same call.
    """

    class Blank:
        async def ask(self, request: ApprovalRequest) -> Answer:
            del request
            return answer_for(deny_with("   "))

    policy = PolicyEngine(
        rules=RuleSet(rules=builtin_rules()),
        asker=Blank(),
        denylist=Denylist(workspace_root=Path("/work"), home=Path("/home/dev")),
    )
    spec = ToolSpec(
        name="bash",
        description="Run a command.",
        danger_level=DangerLevel.DESTRUCTIVE,
        requires_approval=True,
    )

    decision = await policy.approve(
        spec, ToolUse(id="c1", name="bash", arguments={"command": "./deploy.sh"}), rendered="x"
    )

    assert decision.approved is False
    assert "said:" not in decision.reason, "no dangling colon with nothing after it"
    assert "gave no reason" in decision.reason
    # And not the placeholder doubled back on itself: substituting a stand-in here
    # would render "the user declined and said: the user declined this action".
    assert "declined and said" not in decision.reason


@pytest.mark.parametrize("key", ["n", "escape"])
async def test_a_bare_no_tells_the_model_not_to_retry(key: str) -> None:
    """The end-to-end assertion that was missing, and the reason the bug survived.

    Both `n` and `escape` deny without words. The UI-layer tests only ever checked the
    *field* ``decision_for`` produced, so a placeholder in it looked fine there while the
    model was being handed something else entirely:

        the user declined and said: the user declined this action
        Take that as a correction, not a dead end: adjust the plan and continue.

    Two things wrong with that. The placeholder is quoted back as though the human had
    typed it, which no human did. And it takes the branch that invites the model to
    adjust and carry on — the opposite of what a bare "no" means, and the exact opposite
    of the branch the engine has for this case.
    """

    class Bare:
        async def ask(self, request: ApprovalRequest) -> Answer:
            del request
            decision = decision_for(key)
            assert decision is not None
            return answer_for(decision)

    policy = PolicyEngine(
        rules=RuleSet(rules=builtin_rules()),
        asker=Bare(),
        denylist=Denylist(workspace_root=Path("/work"), home=Path("/home/dev")),
    )
    spec = ToolSpec(
        name="bash",
        description="Run a command.",
        danger_level=DangerLevel.DESTRUCTIVE,
        requires_approval=True,
    )

    decision = await policy.approve(
        spec, ToolUse(id="c1", name="bash", arguments={"command": "./deploy.sh"}), rendered="x"
    )

    assert decision.approved is False
    assert "gave no reason" in decision.reason
    assert "Do not retry it" in decision.reason
    # Not quoted back as the human's words, and not the retry-friendly framing.
    assert "declined and said" not in decision.reason
    assert "correction, not a dead end" not in decision.reason


async def refusal_for(asker: Asker) -> str:
    """What the model is told when ``asker`` refuses one gated call."""
    policy = PolicyEngine(
        rules=RuleSet(rules=builtin_rules()),
        asker=asker,
        denylist=Denylist(workspace_root=Path("/work"), home=Path("/home/dev")),
    )
    spec = ToolSpec(
        name="bash",
        description="Run a command.",
        danger_level=DangerLevel.DESTRUCTIVE,
        requires_approval=True,
    )
    decision = await policy.approve(
        spec, ToolUse(id="c1", name="bash", arguments={"command": "./deploy.sh"}), rendered="x"
    )
    assert decision.approved is False
    return decision.reason


class Fixed:
    """An asker that always gives the same answer."""

    def __init__(self, answer: Answer) -> None:
        self._answer = answer

    async def ask(self, request: ApprovalRequest) -> Answer:
        del request
        return self._answer


@pytest.mark.parametrize(
    ("label", "answer"),
    [
        ("unattended", Answer(outcome=Outcome.NO, detail=NO_ANSWER)),
        ("unreadable", Answer(outcome=Outcome.NO, detail=UNREADABLE)),
    ],
)
async def test_a_refusal_with_nobody_behind_it_is_not_quoted_as_the_user_speaking(
    label: str, answer: Answer
) -> None:
    """The bug this pair exists to stop, in the model's own words.

    Through ``feedback`` these rendered as "the user declined and said: no human is
    attached to approve this" — a sentence that contradicts itself — and then "Take that
    as a correction, not a dead end: adjust the plan and continue", which invites a retry
    of a call that cannot succeed. Nobody decided anything, so there is nothing to quote
    and nothing to work from.
    """
    del label
    reason = await refusal_for(Fixed(answer))

    assert "declined and said" not in reason, "nobody said this"
    assert "correction, not a dead end" not in reason, "nothing to correct towards"
    assert "without any decision from the user" in reason
    assert "Do not retry it" in reason


async def test_the_default_asker_no_longer_contradicts_itself() -> None:
    # `UnattendedAsker` is the default, so this was the most-travelled wording of all.
    reason = await refusal_for(UnattendedAsker())

    assert "no human is attached" in reason
    assert "the user declined and said" not in reason


async def test_a_human_who_spoke_still_outranks_a_session_detail() -> None:
    # Both fields set: a person who actually said something wins, because that is the
    # only case where the model has a correction to work from.
    reason = await refusal_for(
        Fixed(Answer(outcome=Outcome.NO, feedback="use staging", detail=NO_ANSWER))
    )

    assert "the user declined and said: use staging" in reason
    assert NO_ANSWER not in reason


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
    # `detail`, not `feedback`: nobody said this, so it must not be quoted back to the
    # model as the user's own words. The engine reproduces `feedback` verbatim.
    assert answer.detail == NO_ANSWER
    assert answer.feedback == ""


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
    # The human was present and typed something, but none of it was an answer — so
    # there is no decision to quote, and this rides `detail`.
    assert answer.detail == UNREADABLE
    assert answer.feedback == ""
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
