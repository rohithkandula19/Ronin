"""Turning standing orders into authority a gate can actually enforce.

``docs/RETAINER.md`` §3.2. This is where the disagreement with the product this
design was drawn against stops being a claim and becomes code: a Retainer's
boundaries are not prose that a model is asked to respect, they are a
:class:`~ronin.safety.policy.RuleSet` and a tool allowlist, sitting on the
unconditional denylist over parsed command segments. The prose still exists — the
model needs to know its own remit — but it is never the enforcement.

Two things here are worth reading before trusting the result.

**A withheld capability removes tools; it does not add a deny rule.** The obvious
implementation is a broad ``deny *`` for anything the deployment cannot do. It
does not work, and the way it fails is quiet: rule precedence is *specificity
first*, so a tool-wide deny (specificity 0) loses to a narrow allow (specificity
2) written in the orders themselves. Orders could talk their way past the wall
one glob at a time. So a capability that was asked for and not held takes its
tools out of :attr:`Authority.tools` instead, and the tool is never published —
absence rather than refusal, which is also the cheapest layer in §3.1 and the
pattern ``cli.serve`` already uses.

**The tool names come from the caller.** Which tools a capability covers is
knowledge the application layer has (``cli.gate`` already names them), and this
package may not import ``cli`` — see the layer graph. Rather than keep a second
copy of those names here and watch it drift, :func:`compile_orders` takes the
mapping as an argument and defaults to knowing nothing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ronin.retainer.model import Capability, Deployment, Retainer, StandingOrders
from ronin.safety.policy import Rule, RuleSet, builtin_ruleset, unattended_rules
from ronin.safety.settings import parse_rule

#: Provenance for rules that came from a Retainer's orders. Rules carry where they
#: came from because a permission nobody can trace is a permission nobody revokes.
ORDERS_SOURCE = "standing orders"

#: What a capability covers, when nobody tells us otherwise. Empty on purpose: see
#: the module docstring. A caller that passes nothing gets no tool removal, not a
#: guess about which tool is which.
NO_CAPABILITY_TOOLS: Mapping[Capability, frozenset[str]] = {}


@dataclass(frozen=True, slots=True)
class Authority:
    """What one Retainer may do on one deployment, compiled.

    ``notes`` is operator-facing and is not decoration. A Retainer that silently
    does less after being moved to a hosted deployment is a support ticket; one
    that says which capability it lost, and therefore which tools went away, is a
    configuration choice somebody can act on.
    """

    ruleset: RuleSet
    tools: frozenset[str]
    granted: frozenset[Capability] = frozenset()
    withheld: frozenset[Capability] = frozenset()
    notes: tuple[str, ...] = ()

    def permits(self, tool: str) -> bool:
        """Whether this tool is published at all. The question §3.1 asks first."""
        return tool in self.tools

    def holds(self, capability: Capability) -> bool:
        return capability in self.granted


def compile_orders(
    orders: StandingOrders,
    deployment: Deployment,
    *,
    capability_tools: Mapping[Capability, frozenset[str]] = NO_CAPABILITY_TOOLS,
    base: RuleSet | None = None,
    narrow_unattended: bool = True,
) -> Authority:
    """Compile standing orders against the deployment they will run on.

    ``base`` defaults to the builtin ruleset, so a Retainer starts from the same
    floor an interactive session does and its orders are a layer on top rather
    than a replacement. Orders are appended, which is what gives them precedence
    among rules of equal specificity — the documented "last wins among equals".

    The default decision comes from the orders. :class:`StandingOrders` defaults
    it to ``ask``, which is what makes escalation possible at all — see that
    field's own note.

    ``narrow_unattended`` applies :func:`~ronin.safety.policy.unattended_rules`,
    which takes back the part of the builtin dev-binary allowance that should
    never have applied with nobody watching: ``npm publish``, ``npm unpublish
    --force``, ``terraform destroy``, ``kubectl delete`` and the rest resolve to
    *allow* today because ``npm``, ``terraform`` and ``kubectl`` are development
    tools. Each narrowing names a subcommand, so ``npm test``, ``terraform plan``
    and ``kubectl get`` keep their allowance.

    **Standing orders cannot punch individual holes in it.** The narrowings tie
    with an orders grant on specificity and win on restrictiveness — which is
    settled before "last wins among equals", so appending a grant does not get
    round it — and they are ``unwaivable``, so a Retainer cannot allow itself
    ``npm publish`` and cannot remember a yes. That is deliberate: a floor the
    restricted thing can edit is not a floor. The escape hatches are answering
    the escalation each time, or
    turning the whole narrowing off here — an operator decision about a
    deployment, not a line in a config the Retainer's own workspace holds.
    """
    granted = orders.granted(deployment)
    withheld = orders.denied(deployment)

    removed: dict[str, Capability] = {}
    for capability in sorted(withheld):
        for tool in capability_tools.get(capability, frozenset()):
            if tool in orders.tools:
                removed[tool] = capability

    notes = tuple(
        f"{tool} is not published: this deployment does not hold the {capability.value} capability"
        for tool, capability in sorted(removed.items())
    ) + tuple(
        f"orders asked for the {capability.value} capability and {deployment.name} does not hold it"
        for capability in sorted(withheld)
    )

    floor = builtin_ruleset() if base is None else base
    narrowings = unattended_rules() if narrow_unattended else ()
    return Authority(
        ruleset=RuleSet(
            # Narrowings sit with the floor they narrow, so orders stay last and
            # "last wins among equals" keeps meaning what it says elsewhere.
            # Position is not what makes them stick: they tie with a grant on
            # specificity and beat it on restrictiveness, and restrictiveness is
            # settled before last-wins is consulted.
            rules=(*floor.rules, *narrowings, *orders.grants),
            default=orders.default,
        ),
        tools=frozenset(orders.tools - set(removed)),
        granted=granted,
        withheld=withheld,
        notes=notes,
    )


def authority_for(
    retainer: Retainer,
    deployment: Deployment,
    *,
    capability_tools: Mapping[Capability, frozenset[str]] = NO_CAPABILITY_TOOLS,
    base: RuleSet | None = None,
    narrow_unattended: bool = True,
) -> Authority:
    """:func:`compile_orders` for a whole Retainer, which is the usual caller."""
    return compile_orders(
        retainer.orders,
        deployment,
        capability_tools=capability_tools,
        base=base,
        narrow_unattended=narrow_unattended,
    )


def parse_grants(entries: Sequence[object], *, source: str = ORDERS_SOURCE) -> tuple[Rule, ...]:
    """Parse a Retainer's rule list from its JSON form.

    A thin pass over :func:`ronin.safety.settings.parse_rule` on purpose. Standing
    orders use the syntax ``settings.json`` already uses — ``{"tool": "bash",
    "decision": "allow", "command": "^pytest"}`` — because a Retainer's orders
    being a second permission language is exactly how the two end up disagreeing
    about what ``path`` means.

    Each entry is reported with its index, since a list of a dozen rules with one
    bad matcher is otherwise a hunt.
    """
    rules: list[Rule] = []
    for index, entry in enumerate(entries):
        try:
            rules.append(parse_rule(entry, source=source))
        except ValueError as exc:
            raise ValueError(f"standing order #{index + 1}: {exc}") from None
    return tuple(rules)


def describe(authority: Authority) -> str:
    """The compiled authority in a form that can be posted into a thread.

    Written for the person deciding whether a Retainer is safe to leave running,
    so it leads with what is published and what was taken away, and lists the
    rules by their own ``describe`` rather than repeating the formatting.
    """
    lines = [f"tools: {', '.join(sorted(authority.tools)) or '(none)'}"]
    if authority.granted:
        lines.append(f"capabilities: {', '.join(sorted(c.value for c in authority.granted))}")
    lines.extend(authority.notes)
    ordered = [rule for rule in authority.ruleset.rules if rule.source == ORDERS_SOURCE]
    lines.extend(f"  {rule.describe()}" for rule in ordered)
    lines.append(f"anything else: {authority.ruleset.default.value}")
    return "\n".join(lines)


__all__ = [
    "NO_CAPABILITY_TOOLS",
    "ORDERS_SOURCE",
    "Authority",
    "authority_for",
    "compile_orders",
    "describe",
    "parse_grants",
]
