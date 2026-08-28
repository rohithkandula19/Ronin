"""The human's answer, carried from the UI to the policy engine.

The loop does not read approvals off the event stream — it asks the injected policy
(``docs/ARCHITECTURE.md`` §8, decision 1), and the policy asks an
:class:`~ronin.safety.policy.Asker`. The TUI, meanwhile, is a pure ``Event`` consumer
that may import ``ronin.core`` and nothing else, so it cannot implement that protocol
itself. This module is the join, and it lives in ``cli`` because ``cli`` is the only
layer allowed to know about both.

**Why the question travels and not the answer.** The first version of this went the other
way: the UI watched for the ``ApprovalRequest`` event, showed a modal, and posted the
answer into a mailbox for the engine to collect. It was simpler and it was wrong. The loop
emits that event for *every* tool whose spec says ``requires_approval`` and only then asks
the policy — which in auto-accept mode allows the call without asking anybody. So a
mailbox fed by the event raises a modal for every edit in the one mode that exists to stop
raising them, and collects an answer that changes nothing. Only the policy knows whether a
human is needed. So the policy asks, and the question is what travels.

**A UI that is not there is a refusal.** Until :meth:`Handoff.attach` is called — no TUI,
a headless run, a nested subagent turn, a session whose front end went away — the answer
is *no*, in the same words :class:`~ronin.safety.policy.UnattendedAsker` uses. The failure
mode of this class is a refused edit, never an unattended approval.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from ..core.types import ApprovalDecision, ApprovalRequest
from ..safety.policy import NO_HUMAN_ATTACHED, Answer, Outcome
from ..ui.app import Asking
from ..ui.reduce import decision_for
from ..ui.render import APPROVAL_PROMPT, render_approval

#: What the model is told when no UI was attached to ask. Now literally the same string
#: as ``UnattendedAsker``'s rather than merely "identical in substance": from the model's
#: side there is no difference between "nobody was asked" and "nobody answered", and two
#: wordings only make one situation look like two problems.
#:
#: It travels as ``Answer.detail``, not ``Answer.feedback`` — nobody said it, so it must
#: not be quoted back as something the user said. It also carries no advice: the engine
#: owns what the model should do next.
NO_ANSWER = NO_HUMAN_ATTACHED


def answer_for(decision: ApprovalDecision) -> Answer:
    """The engine's :class:`Answer` for a UI's :class:`ApprovalDecision`.

    ``remember`` becomes :data:`~ronin.safety.policy.Outcome.YES_SESSION`, not
    ``YES_PERSIST``: a single keystroke should not write a standing rule into
    ``settings.local.json`` that outlives the session and applies on a day nobody
    remembers pressing it. Session scope is the reading of "always" that can be undone
    by closing the terminal, and the engine says which scope it applied in the note it
    attaches, so the human is told what their key actually bought.
    """
    if not decision.approved:
        return Answer(outcome=Outcome.NO, feedback=decision.reason)
    outcome = Outcome.YES_SESSION if decision.remember else Outcome.YES_ONCE
    return Answer(outcome=outcome, feedback=decision.reason)


@dataclass(slots=True)
class Handoff:
    """The seam between a policy engine that asks and a UI that can answer.

    Built before the UI exists — the policy engine is assembled while the workspace loads,
    and the Textual app is started afterwards with the event stream that engine produces —
    so the connection is made late, by :meth:`attach`, and this object is what both sides
    hold on to. Mutable for the same reason
    :class:`~ronin.safety.policy.PolicyEngine` is: one owner, one slot, no globals.
    """

    _ui: Asking | None = field(default=None, repr=False)

    def attach(self, ui: Asking) -> None:
        """Point this at a UI that can ask a human. Called once, by the app, on mount."""
        self._ui = ui

    @property
    def attached(self) -> bool:
        return self._ui is not None

    async def ask(self, request: ApprovalRequest) -> Answer:
        """Satisfies :class:`~ronin.safety.policy.Asker`.

        Waits for as long as the human takes, which is the one place in this codebase
        where an unbounded wait is correct: the alternative is a timeout that approves
        (indefensible) or one that denies work someone was in the middle of reading.
        The turn is already stopped — nothing is running behind this.
        """
        if self._ui is None:
            return Answer(outcome=Outcome.NO, detail=NO_ANSWER)
        return answer_for(await self._ui(request))


#: How many times a line answer may be unreadable before the call is refused. Bounded
#: because an asker that keeps asking is an asker that hangs the turn, and the protocol
#: says it must return. Three is enough for a typo and short of a stuck pipe.
MAX_ATTEMPTS = 3

#: What an unreadable answer is told. Names the keys again rather than only complaining.
REASK = "please answer y (once), a (and remember), or n"

#: Why a refusal happened after :data:`MAX_ATTEMPTS` unreadable answers. A statement of
#: fact carried by ``Answer.detail``: the human was present and typed *something*, but
#: none of it was an answer, so there is no decision to quote and nothing to work from.
UNREADABLE = "the answer typed at the prompt could not be read as yes or no"


@dataclass(slots=True)
class PromptAsker:
    """Asks at a line prompt — the REPL's answer path, and a bare install's only one.

    The Textual modal is the better surface, but it needs the ``tui`` extra and a real
    terminal, and the line session exists precisely for when one of those is missing.
    Without this, a bare install can watch a turn and never approve anything in it.

    The keystroke mapping is :func:`ronin.ui.reduce.decision_for`, the same table the
    modal uses, applied to the first character of the line: one definition of which
    answers approve, so the two surfaces cannot come to disagree about what ``a`` means.
    """

    prompt: Callable[[str], str]
    write: Callable[[str], None]
    attempts: int = MAX_ATTEMPTS

    async def ask(self, request: ApprovalRequest) -> Answer:
        """Satisfies :class:`~ronin.safety.policy.Asker`. Refuses rather than loops."""
        self.write(render_approval(request) + "\n")
        for _ in range(max(self.attempts, 1)):
            typed = self.prompt(f"{APPROVAL_PROMPT} ").strip().lower()
            decision = decision_for(typed[:1]) if typed else None
            if decision is not None:
                return answer_for(decision)
            self.write(REASK + "\n")
        return Answer(outcome=Outcome.NO, detail=UNREADABLE)


__all__ = [
    "MAX_ATTEMPTS",
    "NO_ANSWER",
    "REASK",
    "UNREADABLE",
    "Handoff",
    "PromptAsker",
    "answer_for",
]
