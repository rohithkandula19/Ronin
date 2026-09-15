"""The seam: a summons in, an agent run, an answer out.

``docs/RETAINER.md`` §8 named this the one remaining gap — every piece existed
on both sides and nothing wired them. This is the wiring, and it lives in
``cli/`` because driving :class:`~ronin.cli.sdk.Agent` is the application
layer's job and the layer graph forbids ``ronin.retainer`` from importing here.

The order of operations is the design, so it is worth reading as a list:

1. **Resolve** the Retainer named by the summons. An unknown one is refused
   before anything is cloned or opened.
2. **Bind** the thread to a session, get-or-create. A redelivered webhook lands
   in the conversation that already exists.
3. **Ensure** the post — one checkout per repo per Retainer — and adopt an
   existing one untouched, because it may hold a paused run's work.
4. **Compile** the authority against *this* deployment. Here, at last, the
   capability→tool mapping that :func:`~ronin.retainer.orders.compile_orders`
   takes as an argument is supplied for real, from ``cli.gate``'s own names —
   which is why that argument was injected rather than hardcoded in a package
   that cannot import them.
5. **Open** the agent with that authority: the compiled ruleset as ``rules``,
   the surviving tool set as ``allow_tools``, and a
   :class:`~ronin.retainer.ask.ThreadAsker` so a gated call becomes an
   escalation rather than a flat refusal.
6. **Run**, then read ``exit_code``. ``2`` means an approval was requested and
   this run stopped for it; the escalation the asker recorded is what resumes.
7. **Reply** through the effect ledger, so the answer is posted at most once
   however many times the delivery arrives.

**Nothing is posted directly.** Every outward act goes through
:func:`~ronin.retainer.ledger.once`, and the claim is taken *before* the act.
That is what makes a redelivered webhook harmless rather than merely unlikely.

**The reply is rendered here and posted by an injected callable.** No socket in
this module, so the whole seam is testable with a scripted model and no network
— which the repository requires of every test.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from ..cli.gate import READ_TOOLS, UNTRUSTED_TOOLS
from ..providers.router import Router
from ..retainer.ask import EscalationStore, ThreadAsker
from ..retainer.ledger import EffectLedger, once
from ..retainer.model import (
    Capability,
    Deployment,
    Effect,
    EffectKind,
    Retainer,
    Summons,
    summons_id,
)
from ..retainer.orders import Authority, authority_for
from ..retainer.posts import PostStore
from ..retainer.threads import ThreadMap
from .sdk import Agent, AgentResult

#: Which tools each capability covers. **This** is the mapping
#: :func:`~ronin.retainer.orders.compile_orders` takes as an argument: the names
#: belong to ``cli.gate``, and ``ronin.retainer`` may not import ``cli``, so a
#: copy over there would have been the copy that drifts.
#:
#: ``SHELL`` is ``bash`` alone — every other mutating tool is confined to the
#: workspace by ``ToolContext.resolve`` and gated by policy, whereas a shell is
#: the one that can reach anything the process can.
CAPABILITY_TOOLS: Final[Mapping[Capability, frozenset[str]]] = {
    Capability.SHELL: frozenset({"bash"}),
    Capability.NETWORK: UNTRUSTED_TOOLS,
    # No tool implements it yet; naming it keeps the wall honest rather than
    # letting a granted capability quietly mean nothing.
    Capability.BROWSER: frozenset({"browse"}),
}

#: What a Retainer may always do, capability or not: look. Read-only tools change
#: nothing, so gating them buys nothing and costs an escalation — which is the
#: trade that gets a gate switched off.
ALWAYS_PUBLISHED: Final[frozenset[str]] = READ_TOOLS | frozenset({"glob", "grep"})

#: Posted when a run stopped for an approval. Deliberately not the model's own
#: words: it was refused, so whatever it said next is about the refusal.
PAUSED_TEMPLATE: Final = (
    "Paused — {retainer} asked for an approval and stopped here. "
    "The request is in this thread; nothing further has been done."
)


class RunRefused(RuntimeError):
    """The summons could not be acted on. Carries a reason worth posting."""


#: How a rendered answer reaches the platform it came from. Injected, so this
#: module opens no socket and the adapters keep the wire formats.
Poster = Callable[[Summons, str], Awaitable[str]]


@dataclass(frozen=True, slots=True)
class Stores:
    """The four durable stores one deployment holds, in one argument.

    Grouped because a runner taking eight positional dependencies is a runner
    whose call sites drift out of agreement with each other.
    """

    threads: ThreadMap
    ledger: EffectLedger
    escalations: EscalationStore
    posts: PostStore


@dataclass(frozen=True, slots=True)
class Outcome:
    """What one summons produced. Everything a caller needs to report."""

    summons: Summons
    session: str
    authority: Authority
    result: AgentResult | None = None
    escalations: tuple[str, ...] = ()
    posted: str = ""
    notes: tuple[str, ...] = ()

    @property
    def paused(self) -> bool:
        """Whether this run stopped for an approval rather than finishing."""
        return bool(self.escalations)

    @property
    def ok(self) -> bool:
        return self.result is not None and self.result.ok


def published_tools(authority: Authority) -> frozenset[str]:
    """The tool names this authority actually publishes.

    Read-only tools are added rather than required: standing orders that forgot
    to name ``read`` should still be able to look, and a Retainer that cannot
    look is one that guesses.
    """
    return frozenset(authority.tools | ALWAYS_PUBLISHED)


def _reply_text(result: AgentResult, retainer: Retainer, escalated: bool) -> str:
    if escalated:
        return PAUSED_TEMPLATE.format(retainer=retainer.name)
    text = result.text.strip()
    if text:
        return text
    # An empty answer with a clean exit is still an answer; saying nothing at all
    # would leave the thread looking like the Retainer never woke up.
    return f"{retainer.name} finished with nothing to report."


async def run_summons(
    summons: Summons,
    *,
    retainers: Mapping[str, Retainer],
    deployment: Deployment,
    stores: Stores,
    post: Poster,
    home: Path,
    router: Router | None = None,
    environ: Mapping[str, str] | None = None,
    capability_tools: Mapping[Capability, frozenset[str]] = CAPABILITY_TOOLS,
    max_iterations: int | None = None,
) -> Outcome:
    """Act on one summons, from resolution to a posted reply.

    Returns rather than raising for the ordinary unhappy paths — a run that
    escalated, a model that errored — because those are outcomes a caller has to
    report into a thread. :class:`RunRefused` is for a summons that could not be
    acted on at all, which is a different conversation.

    ``router`` is injected for the reason everything else here is: a seam that can
    only be exercised against a real provider is a seam with no tests, and this
    repository runs its suite offline. ``None`` loads the workspace's own.
    """
    retainer = retainers.get(summons.retainer)
    if retainer is None:
        raise RunRefused(f"no retainer named {summons.retainer!r} on {deployment.name}")
    if not retainer.reachable_on(summons.channel):
        raise RunRefused(
            f"{retainer.id} is not reachable on {summons.channel.value} — "
            "add the channel to its record rather than working around it here"
        )

    held = await stores.posts.ensure(retainer.id, retainer.post.repo, branch=retainer.post.branch)
    binding = stores.threads.bind(
        retainer.id,
        summons.channel,
        summons.thread,
        workspace=held.post.workspace,
    )

    authority = authority_for(retainer, deployment, capability_tools=capability_tools)

    asker = ThreadAsker(
        store=stores.escalations,
        retainer=retainer.id,
        thread=summons.thread,
        session=binding.session,
    )
    budgets = retainer.orders.budgets
    agent = await Agent.open(
        held.post.workspace,
        home=home,
        router=router,
        environ=environ,
        session_id=binding.session,
        asker=asker,
        rules=authority.ruleset.rules,
        allow_tools=published_tools(authority),
    )
    try:
        result = await agent.run(
            summons.text,
            max_iterations=max_iterations if max_iterations is not None else budgets.iterations,
        )
        # Read before closing: `allow_tools` reports a name the workspace does not
        # have as a Note on `Loaded`, and a note nobody surfaces is a note nobody
        # gets — which is the whole reason it was a note and not an exception.
        loaded_notes = tuple(note.line() for note in agent.loaded.notes)
    finally:
        await agent.aclose()

    escalated = tuple(asker.raised)
    text = _reply_text(result, retainer, bool(escalated))
    effect = Effect(
        retainer=retainer.id,
        summons=summons_id(summons),
        step="reply",
        kind=EffectKind.COMMENT,
        target=summons.thread,
        body=text,
    )
    posted = ""
    for claim in once(stores.ledger, effect):
        posted = await post(summons, text)
        stores.ledger.complete(claim, result=posted)

    return Outcome(
        summons=summons,
        session=binding.session,
        authority=authority,
        result=result,
        escalations=escalated,
        posted=posted,
        notes=(*authority.notes, *loaded_notes, *result.notes),
    )


__all__ = [
    "ALWAYS_PUBLISHED",
    "CAPABILITY_TOOLS",
    "PAUSED_TEMPLATE",
    "Outcome",
    "Poster",
    "RunRefused",
    "Stores",
    "published_tools",
    "run_summons",
]
