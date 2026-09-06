"""The records a Retainer is made of.

This module is **logic-free** in the same sense as :mod:`ronin.core.types`: frozen
dataclasses, enums, and validators. No I/O, no clock, no network, no policy
evaluation. Everything above it — compiling standing orders into a ruleset,
persisting a thread map, receiving a webhook — is a separate module, so the shape
of a Retainer can be tested without standing one up.

Four invariants are enforced here rather than documented, because each one has a
failure mode that is silent if it is only written down:

1. **Browser use is a property of where the daemon runs, not of what a Retainer
   is told.** *Amazon v. Perplexity* (9th Cir., 2026-08-04) held that the CFAA's
   safe harbour reaches only agents whose communications pass through the user's
   own computer. :class:`Deployment` therefore refuses to hold
   :data:`Capability.BROWSER` when ``hosted`` is true, and standing orders can
   only ever *request* a capability — :meth:`StandingOrders.granted` intersects
   with what the deployment actually has. A Retainer cannot talk its way into a
   capability its host does not possess.
2. **A blanket allow is not standing orders, it is the absence of them.**
   :class:`StandingOrders` refuses a rule allowing every tool with no match,
   because the whole design rests on authority being enumerated.
3. **An escalation's state and its answer agree**, and the answer is one that
   still means something. An answered escalation carries an answer and an open
   one does not, so no caller guesses whether empty means "not yet" or "they
   said nothing" — and ``yes, for this session`` is refused outright, because the
   session an escalation was raised in has already ended by the time anybody
   answers.
4. **Every outward effect has a key before it has a result.** :class:`Effect`
   computes its own ledger key from content, so an idempotency check cannot be
   skipped by forgetting to pass one.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from ronin.safety.policy import Decision, Outcome, Rule

# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #

#: A Retainer id is a directory name, a thread-map key and a ledger column, so it
#: is restricted to what is safe in all three rather than to what is pretty.
SLUG = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class Capability(StrEnum):
    """A power the *deployment* holds, which standing orders may draw on.

    Deliberately coarse. These are not permissions — :class:`Grant` and the policy
    engine do permissions. A capability answers the earlier question of whether
    the machinery for a class of action exists here at all.
    """

    SHELL = "shell"
    """Run commands in the post. Still gated per command by the denylist."""

    NETWORK = "network"
    """Reach the internet through the egress proxy, never directly."""

    BROWSER = "browser"
    """Drive a real browser against third-party sites. Local deployments only."""


class Channel(StrEnum):
    """Where a Retainer can be spoken to. One value per adapter."""

    GITHUB = "github"
    SLACK = "slack"
    TELEGRAM = "telegram"


class SummonsKind(StrEnum):
    """Why a Retainer woke up.

    All three travel the same path by design, so a routine can never reach
    something a mention could not. The kind is provenance for the audit log and
    for the reply, not a privilege level.
    """

    MENTION = "mention"
    ROUTINE = "routine"
    REPLY = "reply"
    """A human answering an escalation. Carries ``escalation`` on the summons."""


class EscalationState(StrEnum):
    OPEN = "open"
    ANSWERED = "answered"
    EXPIRED = "expired"


class EffectKind(StrEnum):
    """The outward, non-idempotent acts worth a ledger row."""

    COMMENT = "comment"
    PUSH = "push"
    REQUEST = "request"
    NOTIFY = "notify"


# --------------------------------------------------------------------------- #
# Where it runs
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Deployment:
    """One place Retainers run, and what that place is allowed to do.

    Both shapes are supported on purpose — a daemon on your own machine and a
    hosted one — and the difference between them is not merely operational, so it
    is a field rather than a deployment note. See invariant 1 in the module
    docstring.
    """

    name: str
    hosted: bool = False
    capabilities: frozenset[Capability] = frozenset()

    def __post_init__(self) -> None:
        if not SLUG.match(self.name):
            raise ValueError(f"Deployment.name must be a slug, got {self.name!r}")
        if self.hosted and Capability.BROWSER in self.capabilities:
            raise ValueError(
                "a hosted deployment cannot hold the browser capability: the CFAA "
                "safe harbour reaches only agents whose traffic passes through the "
                "user's own computer, so run this Retainer locally or drop the "
                "capability"
            )

    def holds(self, capability: Capability) -> bool:
        return capability in self.capabilities


# --------------------------------------------------------------------------- #
# What it holds
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Post:
    """The workspace a Retainer holds: a checkout and the remote it came from.

    A Post is disposable by design. Grok Bot's equivalent is a durable microVM
    whose loss is an incident; here it is a directory, and losing it costs a
    clone. Nothing that matters is stored only in a Post.
    """

    repo: str
    workspace: Path
    branch: str = ""

    def __post_init__(self) -> None:
        if self.repo.count("/") != 1 or not all(self.repo.split("/")):
            raise ValueError(f"Post.repo must be 'owner/name', got {self.repo!r}")
        if not self.workspace.is_absolute():
            raise ValueError(f"Post.workspace must be absolute, got {self.workspace}")


# --------------------------------------------------------------------------- #
# What it may do
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Budgets:
    """The bounds on one run.

    Low ceilings are the design, not timidity. Measured agent reliability falls
    off a cliff past a few hours of equivalent human work, so a Retainer that
    stops and escalates is worth more than one that keeps going; see
    ``docs/RETAINER.md`` §9. ``notifications`` is separate from the rest because
    acting proactively and interrupting somebody are different budgets.
    """

    iterations: int = 40
    tokens: int = 200_000
    seconds: int = 900
    notifications: int = 4
    """Per day, across every channel. The practical ceiling people tolerate."""

    def __post_init__(self) -> None:
        for name in ("iterations", "tokens", "seconds"):
            if getattr(self, name) < 1:
                raise ValueError(f"Budgets.{name} must be positive")
        if self.notifications < 0:
            raise ValueError("Budgets.notifications cannot be negative")


@dataclass(frozen=True, slots=True)
class StandingOrders:
    """The compiled authority of one Retainer, before compilation.

    ``brief`` is prose and goes in the prompt, because the model needs to know its
    own remit. It is never the enforcement — ``grants``, ``tools`` and the
    unconditional denylist beneath them are. That split is the whole disagreement
    with the product this design was drawn against.
    """

    brief: str = ""
    tools: frozenset[str] = frozenset()
    grants: tuple[Rule, ...] = ()
    """Written as JSON and parsed by :func:`ronin.safety.settings.parse_rule` — the
    same parser and the same syntax as ``settings.json``, deliberately. A Retainer's
    orders being a *second* permission language is how the two drift."""
    default: Decision = Decision.DENY
    """What happens when no grant matches. Denying is the only safe unattended floor."""
    budgets: Budgets = field(default_factory=Budgets)
    wants: frozenset[Capability] = frozenset()
    """Capabilities these orders draw on. A request, never a grant — see :meth:`granted`."""

    def __post_init__(self) -> None:
        for rule in self.grants:
            blanket = rule.tool == "*" and rule.matcher.specificity == 0
            if blanket and rule.decision is Decision.ALLOW:
                raise ValueError(
                    "a blanket allow is not standing orders, it is the absence of "
                    "them: name the tools, or narrow the match"
                )

    def granted(self, deployment: Deployment) -> frozenset[Capability]:
        """The capabilities actually available: what was asked for, intersected.

        Intersection rather than validation on purpose. Moving a Retainer from a
        local daemon to a hosted one should quietly narrow what it can do, not
        refuse to start and tempt somebody into editing the orders until it does.
        """
        return frozenset(self.wants & deployment.capabilities)

    def denied(self, deployment: Deployment) -> frozenset[Capability]:
        """What these orders asked for and this deployment cannot give."""
        return frozenset(self.wants - deployment.capabilities)


# --------------------------------------------------------------------------- #
# Who it is
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Retainer:
    """A named agent in standing service.

    Everything here is durable and edited by a human. Nothing here is a handle to
    a process, a session, or a connection, because a Retainer does not have one
    between turns.
    """

    id: str
    name: str
    post: Post
    orders: StandingOrders
    channels: frozenset[Channel] = frozenset()
    acts_as: str = ""
    """The identity it commits and comments as. Empty means the operator's own."""

    def __post_init__(self) -> None:
        if not SLUG.match(self.id):
            raise ValueError(f"Retainer.id must be a slug, got {self.id!r}")
        if not self.name.strip():
            raise ValueError("Retainer.name is required")

    def reachable_on(self, channel: Channel) -> bool:
        return channel in self.channels


# --------------------------------------------------------------------------- #
# One turn's worth
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Summons:
    """One normalised request to act, whatever produced it.

    The adapters' entire job on the way in is to build one of these. Everything
    downstream sees the same type, which is what makes a third adapter cheap and
    a privileged back door impossible.
    """

    retainer: str
    kind: SummonsKind
    channel: Channel
    thread: str
    """The external conversation id — issue, PR, Slack thread, chat. Maps to a session."""
    text: str = ""
    actor: str = ""
    """Who asked. Untrusted: it is a display name from an external system."""
    escalation: str = ""

    def __post_init__(self) -> None:
        if not SLUG.match(self.retainer):
            raise ValueError(f"Summons.retainer must be a slug, got {self.retainer!r}")
        if not self.thread:
            raise ValueError("Summons.thread is required — it is the session key")
        if self.kind is SummonsKind.REPLY and not self.escalation:
            raise ValueError("a reply summons must name the escalation it answers")
        if self.kind is not SummonsKind.REPLY and self.escalation:
            raise ValueError(f"only a reply summons carries an escalation, not {self.kind.value}")


@dataclass(frozen=True, slots=True)
class Escalation:
    """A persisted request for authority the Retainer does not have.

    The run that raised this one has already ended. ``state`` and ``checkpoint``
    are what let the answer resume it against a base that may have moved: the
    reply is answered with what changed, not replayed blindly. An approval
    controls the proposed action — it does not reverse work already completed.
    """

    id: str
    retainer: str
    thread: str
    tool: str
    request: str
    """What was asked for, in the operator's words rather than the tool's."""
    session: str = ""
    checkpoint: str = ""
    state: EscalationState = EscalationState.OPEN
    answer: Outcome | None = None
    """The human's answer. An :class:`~ronin.safety.policy.Outcome` rather than a
    :class:`~ronin.safety.policy.Decision`, because a person answering has the four
    choices the approval ladder gives them — not the three a rule has."""
    answered_by: str = ""
    """The *verified* id of whoever answered, from the adapter. Never
    :attr:`Summons.actor`, which is a display name an external system supplied."""

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("Escalation.id is required")
        if not self.request.strip():
            raise ValueError("Escalation.request is required — an unexplained ask is a dead end")
        answered = self.state is EscalationState.ANSWERED
        if answered and self.answer is None:
            raise ValueError("an answered escalation must carry the answer")
        if not answered and self.answer is not None:
            raise ValueError(f"a {self.state.value} escalation cannot carry an answer")
        if self.answer is Outcome.YES_SESSION:
            raise ValueError(
                "an escalation outlives the session it was raised in — that run "
                "already ended — so 'yes for this session' has nothing to apply to. "
                "Answer once, or persist it into the standing orders."
            )

    @property
    def open(self) -> bool:
        return self.state is EscalationState.OPEN

    def resolved(self, answer: Outcome, *, by: str = "") -> Escalation:
        """The same escalation, answered. Returns a new record; nothing mutates."""
        if not self.open:
            raise ValueError(f"escalation {self.id} is already {self.state.value}")
        return Escalation(
            id=self.id,
            retainer=self.retainer,
            thread=self.thread,
            tool=self.tool,
            request=self.request,
            session=self.session,
            checkpoint=self.checkpoint,
            state=EscalationState.ANSWERED,
            answer=answer,
            answered_by=by,
        )


@dataclass(frozen=True, slots=True)
class Effect:
    """One outward, non-idempotent act, keyed so it can only happen once.

    Webhooks redeliver and resumed runs replay, so "post the comment" is reached
    more than once for one logical act as a matter of course rather than as a
    bug. The key covers the content as well as the step: a retry of the same
    comment is the same effect, an edited one is not.
    """

    retainer: str
    summons: str
    step: str
    kind: EffectKind
    target: str
    body: str = ""

    def __post_init__(self) -> None:
        for name in ("retainer", "summons", "step", "target"):
            if not getattr(self, name):
                raise ValueError(f"Effect.{name} is required — it is part of the ledger key")

    @property
    def digest(self) -> str:
        """A stable hash of everything that makes this effect this effect."""
        parts = (self.retainer, self.summons, self.step, self.kind.value, self.target, self.body)
        joined = "\x00".join(parts).encode()
        return hashlib.sha256(joined).hexdigest()

    @property
    def key(self) -> tuple[str, str, str, str]:
        """The ledger's primary key: who, which summons, which step, what content."""
        return (self.retainer, self.summons, self.step, self.digest)


def summons_id(summons: Summons, *, nonce: str = "") -> str:
    """A deterministic id for a summons, so a redelivery produces the same one.

    ``nonce`` is for the caller that genuinely has two distinct summonses with
    identical content — a routine firing twice on a schedule, say — and passes
    the fire time. Left empty, two identical deliveries collapse into one id,
    which is the behaviour a webhook receiver wants.
    """
    parts = (
        summons.retainer,
        summons.kind.value,
        summons.channel.value,
        summons.thread,
        summons.text,
        summons.escalation,
        nonce,
    )
    return hashlib.sha256("\x00".join(parts).encode()).hexdigest()[:32]


def by_id(retainers: Sequence[Retainer]) -> Mapping[str, Retainer]:
    """Index retainers by id, refusing duplicates rather than silently dropping one."""
    index: dict[str, Retainer] = {}
    for retainer in retainers:
        if retainer.id in index:
            raise ValueError(f"duplicate retainer id: {retainer.id!r}")
        index[retainer.id] = retainer
    return index
