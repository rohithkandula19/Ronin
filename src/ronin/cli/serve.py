"""``ronin2 mcp-serve`` — Ronin as an MCP server that something else can launch.

:mod:`ronin.mcp.server` has always been able to do this. What it could not do was
*happen*: nothing outside the tests and the demo ever constructed an
:class:`~ronin.mcp.server.McpServer`, so "Claude Desktop, Cursor, or a second Ronin can
drive this one" was true in the suite and false on a machine. This module is the
missing constructor, and :func:`ronin.cli.main.dispatch` is the process that runs it.

Two decisions are made here, and both follow from one fact: **over stdio there is no
human.** stdin carries JSON-RPC frames, so there is nowhere to print "approve? [y/n]"
and nothing to read the answer from.

**Only tools that can actually run are exposed.** A gated tool with no human to ask
would appear in ``tools/list``, be chosen by the client's model, and refuse — every
time, forever. That is the failure ``ronin.cli.wire`` already argues against for
``web_search``: "a search tool with no backend that errors on every call teaches the
model to keep trying it", which is why every group there is opt-in by supplying its
dependency. The dependency here is a permission mode that does not need a human, and
:meth:`ronin.safety.policy.PolicyEngine.relaxes` is asked the question rather than this
module re-deriving the answer. So::

    ronin2 mcp-serve                 read, grep, glob
    ronin2 mcp-serve --mode auto_edit  … and edit
    ronin2 mcp-serve --mode full       … and bash, and ronin_task

and what is withheld is named on stderr, with the flag that would expose it, rather
than being silently absent.

**The whole gate travels, and the whole gate is two things.** Every exposed tool is an
:class:`ExposedTool`, which asks :meth:`~ronin.safety.policy.PolicyEngine.approve` and
*then* runs the call through the session's ``GatedRegistry`` — because those are two
different checks and a session applies both. The registry's pipeline is hooks, the
stale-edit check, taint registration and the output clamp; **approval is not in it**. In
a turn, approval is ``core.loop.run_turn``'s: the loop asks the policy before it calls
the registry at all. Nothing behind a ``tools/call`` is a loop, so the asking is done
here instead. Skip it and ``mcp-serve --mode full`` becomes the one path in Ronin that
reaches the shell without the **deny list** — which lives in the policy engine and
nowhere else — ever being consulted. That was the first version of this file, and the
test that served ``rm -rf ~`` deleted the directory.

Nothing here writes to stdout. That is not a style rule: stdout *is* the protocol, and
one stray line of prose corrupts the frame stream — the failure
:class:`~ronin.mcp.transport.StdioTransport` treats as fatal, from the other side.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ..core.protocols import ToolRegistry as ToolRegistryProtocol
from ..core.types import (
    ApprovalDecision,
    ApprovalRequest,
    Mode,
    ToolResult,
    ToolSpec,
    ToolUse,
)
from ..mcp.server import (
    DEFAULT_EXPOSED_TOOLS,
    RONIN_TASK,
    Approver,
    McpServer,
    NestedRunner,
    RoninTaskTool,
    render_call,
)
from ..safety.policy import PolicyEngine
from ..tools.base import Tool, ToolContext
from ..tools.registry import ToolRegistry
from .sdk import Agent

#: Reason on the decision this module's approver returns. Names the mode, because the
#: mode is what actually decided: the tool was exposed at startup precisely because
#: this mode does not require a human for it.
MODE_APPROVES = "{mode} mode allows {name} without asking, which is why it is exposed"

#: Reason for the refusal. Reached only if a tool that needs a human is somehow called
#: — the exposed set is filtered, so this is the belt to that braces.
MODE_REFUSES = (
    "{name} needs a human to approve it and an MCP stdio server has none: stdin "
    "carries the protocol, so there is nowhere to ask. Start the server with a more "
    "permissive --mode if this tool should run unattended."
)

#: What a nested run reports when it produced no prose at all.
NESTED_SILENT = "the nested loop ended with no summary to report (stop reason: {reason})"

#: Appended when a nested run did not end cleanly. The caller gets the text *and* the
#: fact that it is partial — a summary that omits "this run was cut short" is worse
#: than no summary.
NESTED_UNCLEAN = (
    "[the nested run exited {code} (stop reason: {reason}); this summary may be partial]"
)

#: How ``mcp-serve``'s banner names the flag that would widen the toolset. One string so
#: the message and the mode ladder cannot disagree.
WIDEN = "`--mode {mode}` exposes {names}"


class ExposedTool(Tool):
    """One tool published over MCP, run the way a turn would run it.

    **Two checks, because a session has two.** ``GatedRegistry`` is not the whole gate:
    its pipeline is hooks, the stale-edit check, taint registration and the output
    clamp — and *not* approval. In a normal session the approval is
    ``core.loop.run_turn``'s, which asks ``Policy.approve`` before it calls the registry
    at all (see ``docs/ARCHITECTURE.md`` §4: the loop asks, the policy answers). A
    direct ``tools/call`` has no loop behind it, so this class asks in the loop's place,
    with the same call and in the same order.

    That is not a belt-and-braces flourish. Without it, ``mcp-serve --mode full`` would
    reach the shell without the **deny list** ever being consulted — the one refusal in
    Ronin that is unconditional — because the deny list lives in the policy engine and
    nothing else in the path looks at it. It was written the other way round first, and
    a test that served ``rm -rf ~`` deleted the directory.

    The ``ctx`` handed to :meth:`run` is deliberately discarded. The gated registry has
    the session's own :class:`~ronin.tools.base.ToolContext` bound in — the root every
    path is resolved against, and the read-before-write ledger ``edit`` consults — and
    routing a call through a *second* context would give the remote client its own
    ledger, in which no file has been read and therefore none may be written. Worse, it
    would resolve paths against a root nobody chose.
    """

    def __init__(
        self, spec: ToolSpec, registry: ToolRegistryProtocol, policy: PolicyEngine
    ) -> None:
        self._spec = spec
        self._registry = registry
        self._policy = policy
        self._calls = 0
        # Instance attribute over the class one: `Tool.execute`'s error boundary names
        # `self.name`, and every ExposedTool is a different tool.
        self.name = spec.name

    def spec(self) -> ToolSpec:
        """The inner tool's spec, verbatim — including its danger level.

        Not rebuilt from this class's attributes, which is what makes the level and the
        approval flag that :meth:`McpServer.tool_descriptors` publishes the *real* ones.
        """
        return self._spec

    async def run(self, args: Mapping[str, object], ctx: ToolContext) -> ToolResult:
        self._calls += 1
        use = ToolUse(
            # Ids are the gate's audit key, so they are unique per call rather than per
            # tool: `/doctor`'s log would otherwise show one row per name.
            id=f"mcp-{self._spec.name}-{self._calls}",
            name=self._spec.name,
            arguments=args,
        )
        if self._spec.requires_approval:
            decision = await self._policy.approve(
                self._spec, use, rendered=render_call(self._spec.name, args)
            )
            if not decision.approved:
                # The same `DENIED:` shape `core.loop` produces, so a refusal reads the
                # same whether it reached the model through a turn or over the wire.
                detail = decision.reason or "the policy declined this action"
                return ToolResult(ok=False, error=f"DENIED: {detail}")
        return await self._registry.execute(use)


def nested_runner(agent: Agent) -> NestedRunner:
    """``ronin_task``'s implementation: one prompt through the whole agent loop.

    Shares ``agent``'s conversation rather than starting a fresh one per call, so a
    client can say "fix the failing test" and then "now update the docs" and be
    understood. That is also what puts nested runs under compaction and inside one
    transcript; a new conversation per call would re-read the workspace every time and
    record each task as an unrelated session.
    """

    async def run(prompt: str) -> str:
        result = await agent.run(prompt)
        text = result.text.strip()
        if not text:
            return NESTED_SILENT.format(reason=result.stop_reason or "unknown")
        if result.ok:
            return result.text
        unclean = NESTED_UNCLEAN.format(
            code=result.exit_code, reason=result.stop_reason or "unknown"
        )
        return f"{result.text}\n\n{unclean}"

    return run


def mode_approver(policy: PolicyEngine, specs: Mapping[str, ToolSpec]) -> Approver:
    """An :data:`~ronin.mcp.server.Approver` that answers with the permission mode.

    Not "approve everything": it looks the named tool up and asks the live
    :class:`~ronin.safety.policy.PolicyEngine` again. A tool absent from ``specs``, or
    one the mode does not relax, is refused — so if the exposed set and the approver
    ever disagree, they disagree in the safe direction. Asking the *live* engine also
    means a mode narrowed mid-session takes effect on the next call.
    """

    async def approve(request: ApprovalRequest) -> ApprovalDecision:
        spec = specs.get(request.name)
        if spec is None or not policy.relaxes(spec):
            return ApprovalDecision(approved=False, reason=MODE_REFUSES.format(name=request.name))
        return ApprovalDecision(
            approved=True,
            reason=MODE_APPROVES.format(mode=policy.mode.value, name=request.name),
        )

    return approve


@dataclass(frozen=True, slots=True)
class Served:
    """An assembled server plus everything the startup banner has to say.

    The three name lists are separate because they are three different facts. A tool
    can be *withheld* (this session has it; the mode needs a human for it), *missing*
    (this session was built without it — no shell, no ``bash``), or exposed. Collapsing
    withheld and missing into "not available" would answer "why can't I edit?" with the
    one thing that does not distinguish "raise the mode" from "you cannot".
    """

    server: McpServer
    mode: Mode
    root: str
    exposed: tuple[str, ...] = ()
    withheld: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()

    def banner(self) -> str:
        """What ``mcp-serve`` prints to **stderr** before the first frame.

        Ends with a newline and never touches stdout — see the module docstring.
        """
        lines = [
            f"ronin mcp-serve: {len(self.exposed)} tool(s) over stdio, "
            f"mode={self.mode.value}, root={self.root}"
        ]
        lines.append("  exposed: " + (", ".join(self.exposed) or "none"))
        if self.withheld:
            lines.append(
                f"  withheld: {', '.join(self.withheld)} — {self.mode.value} mode needs a "
                "human to approve these and stdio has no human to ask."
            )
            widen = _widening(self.withheld)
            if widen:
                lines.append(f"  {widen}")
        if self.missing:
            lines.append(
                f"  absent from this session: {', '.join(self.missing)} — it was built "
                "without them, so no mode exposes them."
            )
        if not self.exposed:
            lines.append(
                "  nothing is exposed, so a client will see an empty tool list. That is "
                "reported rather than treated as a failure: the handshake still works, "
                "which is what tells a client the server is alive."
            )
        return "\n".join(lines) + "\n"


def build_server(
    agent: Agent,
    *,
    version: str = "",
    exposed: Sequence[str] = DEFAULT_EXPOSED_TOOLS,
    task: bool = True,
) -> Served:
    """Assemble the MCP server this session can honestly offer.

    ``exposed`` names the *candidates*; what comes back is the subset this session both
    has and can run unattended. ``task=False`` leaves ``ronin_task`` out even when the
    mode would allow it, which is how a caller who wants tool access without a nested
    agent says so.
    """
    runtime = agent.runtime
    policy = runtime.policy
    gate = runtime.registry
    published: dict[str, ToolSpec] = {}
    tools: list[Tool] = []
    withheld: list[str] = []
    missing: list[str] = []

    for name in exposed:
        spec = gate.get(name)
        if spec is None:
            missing.append(name)
            continue
        if not policy.relaxes(spec):
            withheld.append(name)
            continue
        published[name] = spec
        tools.append(ExposedTool(spec, gate, policy))

    # Snapshotted before `ronin_task` joins `published`: that one is constructed by
    # `McpServer` itself and is not in the adapter registry, so passing its name in
    # `exposed=` would have it recorded as an unavailable tool.
    adapters = tuple(published)

    runner = nested_runner(agent) if task else None
    if runner is not None:
        # Built to be asked for its spec, then either kept or dropped. Reading the class
        # attributes here instead would be a copy of RoninTaskTool's danger level, and
        # the copy is what would still say DESTRUCTIVE after somebody changed the tool.
        task_spec = RoninTaskTool(runner).spec()
        if policy.relaxes(task_spec):
            published[RONIN_TASK] = task_spec
        else:
            withheld.append(RONIN_TASK)
            runner = None

    # An approver only when something actually needs one. Attaching one unconditionally
    # would publish `ronin/approverAttached: true` on a server where nothing is gated,
    # which tells a client the opposite of the truth.
    needs_approval = any(spec.requires_approval for spec in published.values())
    server = McpServer(
        # A registry of adapters, so `McpServer` finds every name it is given and the
        # ToolContext it binds is never reached — see `ExposedTool.run`.
        ToolRegistry(tools, ToolContext(root=agent.loaded.paths.workspace_root)),
        nested_runner=runner,
        exposed=adapters,
        approver=mode_approver(policy, published) if needs_approval else None,
        version=version or "unknown",
    )
    return Served(
        server=server,
        mode=policy.mode,
        root=str(agent.loaded.paths.workspace_root),
        exposed=tuple(published),
        withheld=tuple(withheld),
        missing=tuple(missing),
    )


def _widening(withheld: Sequence[str]) -> str:
    """The mode that would expose the withheld tools, as one line — or ``""``.

    Two rungs rather than a general search: ``auto_edit`` is the one that adds ``edit``,
    and ``full`` is the one that adds everything else. Naming the *lowest* mode that
    helps matters, because pointing someone at ``--mode full`` to get ``edit`` hands
    them ``bash`` they did not ask for.
    """
    rest = [name for name in withheld if name != "edit"]
    if "edit" in withheld and not rest:
        return WIDEN.format(mode=Mode.AUTO_EDIT.value, names="it")
    if not withheld:
        return ""
    return WIDEN.format(mode=Mode.FULL.value, names=", ".join(withheld))


__all__ = [
    "MODE_APPROVES",
    "MODE_REFUSES",
    "NESTED_SILENT",
    "NESTED_UNCLEAN",
    "ExposedTool",
    "Served",
    "build_server",
    "mode_approver",
    "nested_runner",
]
