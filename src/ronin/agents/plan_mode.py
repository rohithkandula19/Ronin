"""plan mode: a registry that *cannot* edit, and an approved plan that becomes law.

Two ideas, and the first one is the only one that is load-bearing.

**Read-only is a property of the registry, not of the prompt.** A model told "you
are in plan mode, do not edit" will eventually edit — three turns later, mid-way
through a long think, when the instruction has scrolled past the interesting part
of the context. So plan mode is entered by building a *narrower*
:class:`~ronin.tools.registry.ToolRegistry` through ``subset()``: ``write``,
``edit``, ``multi_edit``, ``bash`` and ``bash_output`` are not in it. A tool that
is not in the registry cannot be called, cannot be described to the model, and
cannot be reached by a hallucinated name — ``execute`` answers with "there is no
tool called 'write'". :func:`assert_read_only` then re-derives the guarantee from
the specs, so the invariant is *checked* rather than merely constructed: a
mislabelled tool, or a caller that widened ``names``, fails at entry instead of
at the first destructive call.

**Approval flips the mode and pins the plan.** The plan is not advice the model
may reinterpret once it starts working; it is the standing objective for the rest
of the session. That is why :func:`pinned_objective` exists as a rendering
function over a frozen :class:`Plan`: the same text goes into context every turn,
so "what am I doing" cannot drift as the transcript grows.

Parsing the plan out of prose is deliberately tolerant — models write numbered
lists, bullets, headings, and bold — but never lossy: an unparseable answer is a
:class:`PlanParseError` carrying the raw text, because a silently empty plan
approves nothing and then the agent works with no objective at all.

The ``plan`` model role belongs here (a reasoning model earns its cost in exactly
one place: deciding what to do before anything is touched). This package may not
import ``ronin.providers``, so the role travels as the *name* in
:data:`PLAN_MODEL_ROLE` and the orchestrator resolves it against its router.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from ..core.types import DangerLevel, Message, Mode, Role, Text
from ..tools.base import ToolContext
from ..tools.registry import ToolRegistry

#: The tools plan mode offers. ``task`` is included because delegating a sweep to
#: a read-only subagent is how a plan gets researched without burning the planning
#: context — see the caveat in :func:`plan_registry` about the child's registry.
PLAN_MODE_TOOLS: tuple[str, ...] = ("read", "glob", "grep", "task")

#: Names that must never appear in a plan-mode registry, checked by name as well
#: as by danger level. Belt and braces on purpose: the danger-level check catches
#: an unknown mutating tool, and the name check catches a *known* one that was
#: mislabelled ``READ_ONLY`` — neither check subsumes the other.
FORBIDDEN_IN_PLAN_MODE: frozenset[str] = frozenset(
    {"write", "edit", "multi_edit", "bash", "bash_output"}
)

#: The router role name for planning. A *name*, not an import: see the module
#: docstring for why ``agents`` cannot see ``ronin.providers``.
PLAN_MODEL_ROLE = "plan"

#: Past this a "plan" is a transcript and the user stops reading it, which defeats
#: the point of asking for approval at all.
MAX_PLAN_STEPS = 40

PLAN_MODE_PROMPT = """\
You are in PLAN MODE. You can read, search, and delegate read-only research, and \
you have no tools that change anything — no write, no edit, no bash. Do not \
describe an edit as though you had made it.

Produce a plan the user can approve or correct:

- A short paragraph on what you found and why this approach, before the steps.
- Then the steps as a numbered list, in the order you will do them. Each step \
names the file or files it touches and what changes in them.
- Name what you are unsure about. A plan with an open question in it is more \
useful than a confident plan that is wrong.

Keep it to the steps you would actually take. Once the user approves, this plan \
becomes the objective you work to, so a step you did not mean is a step you will \
be held to."""


class PlanModeViolation(RuntimeError):
    """A registry claiming to be read-only contains something that is not.

    Raised at *entry* to plan mode, where it is a config error a developer can
    fix, rather than at the first call, where it would already be a mutation.
    """


class PlanParseError(ValueError):
    """The model's answer contained no plan. Carries the raw text.

    The raw text is preserved because the recovery is to show the user what the
    model actually said (or to re-ask), and a stripped error message throws away
    the only evidence of what went wrong.
    """

    def __init__(self, reason: str, raw: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.raw = raw


# --------------------------------------------------------------------------- #
# The registry guarantee
# --------------------------------------------------------------------------- #


def assert_read_only(registry: ToolRegistry) -> None:
    """Raise :class:`PlanModeViolation` unless every tool is ``READ_ONLY``.

    Inspects the specs the model would be shown, so this is the same view the
    provider gets. Two independent checks — see :data:`FORBIDDEN_IN_PLAN_MODE`.
    """
    offenders = [
        f"{spec.name} ({spec.danger_level.name})"
        for spec in registry.specs()
        if spec.danger_level > DangerLevel.READ_ONLY
    ]
    by_name = [spec.name for spec in registry.specs() if spec.name in FORBIDDEN_IN_PLAN_MODE]
    if by_name:
        offenders.extend(f"{name} (forbidden by name)" for name in by_name)
    if offenders:
        raise PlanModeViolation(
            "plan mode registry is not read-only: "
            + ", ".join(sorted(set(offenders)))
            + ". Build it with plan_registry() instead of widening it by hand."
        )
    gated = [spec.name for spec in registry.specs() if spec.requires_approval]
    if gated:
        raise PlanModeViolation(
            f"plan mode registry contains tools that require approval: {sorted(gated)}. "
            "Nothing in plan mode should ever need a human to say yes, because "
            "nothing in plan mode changes anything."
        )


def plan_registry(
    base: ToolRegistry,
    *,
    names: Sequence[str] = PLAN_MODE_TOOLS,
    ctx: ToolContext | None = None,
) -> ToolRegistry:
    """A read-only registry for planning, derived from ``base`` by ``subset()``.

    An unknown name raises (``subset``'s behaviour) rather than being skipped: a
    session built without ``task`` should be told to pass its own ``names``, not
    handed a quietly smaller toolset. Pass ``ctx`` to give planning its own
    ``read_files`` set.

    One honest limit: ``task``'s own spec is read-only, but a *subagent* is only
    read-only if the registry it is subsetted from is. When a session enters plan
    mode it must hand this registry — not the full one — to its subagent runner,
    or a child with ``write`` in its type re-opens the door the parent closed.
    """
    restricted = base.subset(list(names), ctx=ctx)
    assert_read_only(restricted)
    return restricted


@dataclass(frozen=True, slots=True)
class PlanModeSetup:
    """Everything the orchestrator needs to run one planning turn.

    A value rather than a call into the router, because ``agents`` must not import
    ``ronin.providers``: ``model_role`` is the name the caller resolves.
    """

    registry: ToolRegistry
    model_role: str = PLAN_MODEL_ROLE
    mode: Mode = Mode.PLAN
    system_prompt: str = PLAN_MODE_PROMPT


def enter_plan_mode(
    base: ToolRegistry,
    *,
    names: Sequence[str] = PLAN_MODE_TOOLS,
    ctx: ToolContext | None = None,
) -> PlanModeSetup:
    """Build the plan-mode setup from a session's full registry."""
    return PlanModeSetup(registry=plan_registry(base, names=names, ctx=ctx))


# --------------------------------------------------------------------------- #
# The plan
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class PlanStep:
    """One step, renumbered from 1 in the order it appeared."""

    index: int
    text: str

    def __post_init__(self) -> None:
        if self.index < 1:
            raise ValueError("PlanStep.index is 1-based")
        if not self.text.strip():
            raise ValueError("PlanStep.text is empty — an empty step approves nothing")


@dataclass(frozen=True, slots=True)
class Plan:
    """An approvable plan: ordered steps, the reasoning, and the raw answer.

    ``raw`` is kept so the user can be shown what the model actually wrote when
    the parse looks wrong, and so an approval is auditable against the text the
    human read rather than against our re-rendering of it.
    """

    steps: tuple[PlanStep, ...]
    rationale: str = ""
    raw: str = ""

    def __post_init__(self) -> None:
        if not self.steps:
            raise ValueError(
                "a Plan with no steps cannot be approved — construct it with "
                "parse_plan(), which raises PlanParseError and keeps the raw text"
            )
        if len(self.steps) > MAX_PLAN_STEPS:
            raise ValueError(f"{len(self.steps)} steps is not a plan ({MAX_PLAN_STEPS} max)")
        for position, step in enumerate(self.steps, start=1):
            if step.index != position:
                raise ValueError(
                    f"Plan.steps must be numbered 1..n in order; step {position} "
                    f"carries index {step.index}"
                )

    def render(self) -> str:
        """The steps as the user reads them before approving."""
        return "\n".join(f"{step.index}. {step.text}" for step in self.steps)


_FENCE = re.compile(r"^\s{0,3}(?:```|~~~)")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*")
#: ``1.`` / ``1)`` / ``Step 1:`` — the marker must be followed by whitespace, so
#: "3.5x slower" in a rationale is not read as step 3.
_NUMBERED = re.compile(r"^\s{0,3}(?:step\s+)?(\d{1,3})\s*[.):]\s+(.*\S)\s*$", re.IGNORECASE)
_BULLET = re.compile(r"^\s{0,3}[-*•+]\s+(.*\S)\s*$")
_RATIONALE_PREFIX = re.compile(r"^(?:rationale|why|reasoning|approach)\s*:\s*", re.IGNORECASE)
_EMPHASIS = re.compile(r"\*\*|__|`")


def _clean(text: str) -> str:
    """Strip markdown emphasis and trailing punctuation noise from a step body."""
    return _EMPHASIS.sub("", text).strip().rstrip(";").strip()


def parse_plan(text: str) -> Plan:
    """Parse a plan out of the model's prose. Tolerant, but never silently empty.

    Accepts numbered lists, bullets, ``Step 1:`` labels and any of those under
    markdown headings. Fenced code blocks are treated as continuation of the
    current step, never as new steps — a diff full of ``-`` lines inside a fence
    would otherwise explode into dozens of one-character "steps", which is a real
    thing models emit when asked to plan a refactor.

    Prose before the first step becomes ``rationale``; unindented prose after the
    list is appended to it. Raises :class:`PlanParseError` — with ``raw`` intact —
    when nothing parses as a step.
    """
    if not text.strip():
        raise PlanParseError("the model returned no text, so there is no plan to approve", text)

    preamble: list[str] = []
    trailing: list[str] = []
    bodies: list[list[str]] = []
    in_fence = False

    for line in text.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
            if bodies:
                bodies[-1].append(line.strip())
            continue
        if in_fence:
            if bodies:
                bodies[-1].append(line.strip())
            continue

        is_heading = _HEADING.match(line) is not None
        stripped = _HEADING.sub("", line).strip()
        if not stripped:
            continue

        numbered = _NUMBERED.match(stripped)
        bullet = None if numbered else _BULLET.match(stripped)
        if numbered is not None:
            bodies.append([_clean(numbered.group(2))])
            continue
        if bullet is not None:
            bodies.append([_clean(bullet.group(1))])
            continue

        if is_heading:
            # A heading is structure, not reasoning. "## Plan" landing in the
            # rationale reads as though the model said the word "Plan" to us.
            continue
        if not bodies:
            preamble.append(stripped)
        elif line[:1] in {" ", "\t"}:
            # An indented continuation line belongs to the step above it; a model
            # writing a two-line step is not writing two steps.
            bodies[-1].append(stripped)
        else:
            trailing.append(stripped)

    steps = tuple(
        PlanStep(index=position, text=" ".join(part for part in body if part).strip())
        for position, body in enumerate(
            [body for body in bodies if any(part.strip() for part in body)], start=1
        )
    )
    if not steps:
        raise PlanParseError(
            "no plan steps found: expected a numbered list or bullets, one step per "
            "line. The model's raw answer is preserved on the error.",
            text,
        )
    if len(steps) > MAX_PLAN_STEPS:
        raise PlanParseError(
            f"{len(steps)} steps parsed, which is more than a reviewable plan "
            f"({MAX_PLAN_STEPS} max) — the answer is probably prose being read as a list",
            text,
        )

    rationale_lines = [_RATIONALE_PREFIX.sub("", line) for line in (*preamble, *trailing)]
    rationale = " ".join(line for line in rationale_lines if line).strip()
    return Plan(steps=steps, rationale=_EMPHASIS.sub("", rationale), raw=text)


def pinned_objective(plan: Plan) -> str:
    """The standing objective text, pinned into context for the rest of the session.

    Says "approved" and says not to renegotiate, because the failure mode after
    approval is not disobedience — it is a model that reads its own plan as a
    suggestion and improves on it three steps in, at which point the user has
    approved one thing and received another.
    """
    lines = [
        "STANDING OBJECTIVE — the user approved this plan. Work to it.",
        "",
        plan.render(),
    ]
    if plan.rationale:
        lines += ["", f"Why: {plan.rationale}"]
    lines += [
        "",
        "Do the steps in order. If a step turns out to be wrong, impossible, or "
        "already done, say so and stop — do not substitute a different plan. "
        "Anything not in these steps needs the user to agree to it first.",
    ]
    return "\n".join(lines)


def objective_message(plan: Plan, *, role: Role = Role.USER) -> Message:
    """The pinned objective as a transcript message.

    Defaults to ``USER`` rather than ``SYSTEM`` because the system prompt is
    assembled by the provider layer from a stable prefix, and appending to it per
    turn would break prefix caching; a user-role reminder rides along with the
    transcript and survives compaction as ordinary content.
    """
    return Message(role=role, content_blocks=(Text(pinned_objective(plan)),))


@dataclass(frozen=True, slots=True)
class PlanApproval:
    """A human's decision on a plan, and the mode the session continues in.

    Frozen, and it carries the plan: "approved" with no record of *what* was
    approved is unauditable, and after a compaction the transcript may no longer
    contain the plan the user actually read.
    """

    plan: Plan
    approved: bool
    note: str = ""
    execute_mode: Mode = Mode.AUTO_EDIT

    def __post_init__(self) -> None:
        if self.approved and self.execute_mode is Mode.PLAN:
            raise ValueError(
                "approving a plan means leaving plan mode; execute_mode=PLAN would "
                "approve a plan the session still cannot act on"
            )

    @property
    def mode(self) -> Mode:
        """The mode the session should be in after this decision."""
        return self.execute_mode if self.approved else Mode.PLAN

    @property
    def objective(self) -> str:
        """The pinned objective, or empty if the plan was rejected."""
        return pinned_objective(self.plan) if self.approved else ""


def approve(plan: Plan, *, note: str = "", execute_mode: Mode = Mode.AUTO_EDIT) -> PlanApproval:
    """Approve ``plan`` and flip to ``execute_mode`` with the plan pinned."""
    return PlanApproval(plan=plan, approved=True, note=note, execute_mode=execute_mode)


def reject(plan: Plan, *, note: str = "") -> PlanApproval:
    """Reject ``plan``. The session stays in plan mode, so nothing can be touched."""
    return PlanApproval(plan=plan, approved=False, note=note)
