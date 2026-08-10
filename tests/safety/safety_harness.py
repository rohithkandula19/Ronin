"""Shared fixtures for the safety suite.

Named ``safety_harness`` rather than ``harness``: the test trees are deliberately not
packages, so every module in them lives in one flat namespace and a second ``harness.py``
would shadow ``tests/tools/harness.py``.
"""

from __future__ import annotations

from pathlib import Path

from ronin.core.types import ApprovalRequest, DangerLevel, ToolSpec, ToolUse
from ronin.safety.denylist import Denylist
from ronin.safety.injection import TaintTracker
from ronin.safety.policy import (
    Answer,
    AnyUse,
    Decision,
    Outcome,
    PolicyEngine,
    Rule,
    RuleSet,
    builtin_rules,
)

WORKSPACE = Path("/work/repo")
HOME = Path("/home/dev")

BASH = ToolSpec(
    name="bash",
    description="Run a shell command in a persistent bash session.",
    danger_level=DangerLevel.DESTRUCTIVE,
    requires_approval=True,
)

WRITE = ToolSpec(
    name="write",
    description="Write a file.",
    danger_level=DangerLevel.MUTATING,
)

READ = ToolSpec(
    name="read",
    description="Read a file.",
    danger_level=DangerLevel.READ_ONLY,
)


def use(command: str, *, tool: str = "bash", **arguments: object) -> ToolUse:
    """A ``ToolUse`` for a command-shaped tool, or for any tool via ``arguments``."""
    args: dict[str, object] = dict(arguments)
    if command:
        args["command"] = command
    return ToolUse(id="call_1", name=tool, arguments=args)


class RecordingAsker:
    """Answers from a script and remembers every question it was asked.

    The point of injecting the asker: the whole approval path is exercised with no UI,
    no terminal, and no human, so "what would the user have been shown" is an assertion
    rather than a screenshot.
    """

    def __init__(self, *answers: Answer) -> None:
        self.answers = list(answers)
        self.requests: list[ApprovalRequest] = []

    async def ask(self, request: ApprovalRequest) -> Answer:
        self.requests.append(request)
        if self.answers:
            return self.answers.pop(0)
        return Answer(outcome=Outcome.NO, feedback="no answer scripted")

    @property
    def asked(self) -> bool:
        return bool(self.requests)


def denylist(*, has_checkpoint: bool = False, yolo: bool = False) -> Denylist:
    return Denylist(
        workspace_root=WORKSPACE,
        home=HOME,
        has_checkpoint=lambda: has_checkpoint,
        yolo=yolo,
    )


def engine(
    *answers: Answer,
    rules: RuleSet | None = None,
    taint: TaintTracker | None = None,
    has_checkpoint: bool = False,
    yolo: bool = False,
    **kwargs: object,
) -> PolicyEngine:
    """A real engine with the builtin ruleset and a scripted asker."""
    asker = RecordingAsker(*answers)
    return PolicyEngine(
        rules=rules if rules is not None else RuleSet(rules=builtin_rules()),
        asker=asker,
        denylist=denylist(has_checkpoint=has_checkpoint, yolo=yolo),
        taint=taint,
        **kwargs,  # type: ignore[arg-type]  # mode/sandbox/persist, checked by callers
    )


def permissive_ruleset() -> RuleSet:
    """A hostile configuration: the project allows the shell tool-wide.

    Used by the adversarial suite. If a command is still refused under *this* ruleset,
    it is refused because of the deny list or a structural hazard — which is the claim
    worth testing, since a user really can write this file.
    """
    return RuleSet(
        rules=(
            *builtin_rules(),
            Rule(
                tool="bash",
                matcher=AnyUse(),
                decision=Decision.ALLOW,
                source="project",
                reason="the user allowed the shell wholesale",
            ),
        ),
        default=Decision.ALLOW,
    )


def asker_of(policy: PolicyEngine) -> RecordingAsker:
    """The engine's asker, narrowed for assertions."""
    assert isinstance(policy.asker, RecordingAsker)
    return policy.asker
