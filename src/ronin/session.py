"""Where the three layers meet: the loop, the providers, and the tools.

This is the orchestrator seat in ``docs/ARCHITECTURE.md`` §0, and it is the *only*
module allowed to import all three. That is the point of it existing at all —
``core.loop`` takes an injected ``ModelClient`` and ``ToolRegistry`` precisely so it
never has to know where they came from, and ``tools.task`` takes an injected
``SubagentRunner`` for the same reason. Something has to do the introduction, and
putting it anywhere else would put a cycle between two layers the architecture keeps
apart.

What it wires: ``task(prompt, subagent_type)`` → a nested ``run_turn`` on
``router.for_subagent()``, which is the ``fast`` model by construction — unless the
subagent's own definition on disk names a different role, which is decision 1.

Five decisions are encoded here rather than left to the caller, because each is a
place where the obvious wiring is subtly wrong:

1. **The fast model by default, and only a file on disk may say otherwise.**
   ``for_subagent()`` takes no role argument, so a ``task`` call cannot spend the
   main model on search triage. A ``SubagentType`` carrying a ``model_role`` — which
   only reaches here from a ``.ronin/agents/*.md`` definition the user wrote — is
   honoured, because the honest default is wrong for one case: the ``fixer``, which
   edits code and reruns a test, is not mechanical work, and running it cheap spends
   more tokens than it saves. The model still cannot choose its own tier.
2. **A subagent cannot spawn a subagent.** ``task`` is stripped from every child
   registry. Depth-1 is a design limit, not an oversight — unbounded nesting turns
   one user request into an unbounded fan-out with no budget that sees the whole tree.
3. **A subagent cannot escalate to the user, but it can act on standing permission.**
   The parent's approval flow is not reachable from inside a nested turn, so
   forwarding would either hang or put a prompt in front of a user who asked for
   something else three steps ago. The default policy therefore denies every gated
   call — with a reason the child can report. But "denies everything gated" made the
   builtin ``fixer`` unable to run the test it exists to fix: it declares ``bash``,
   and every ``bash`` call came back refused. So the policy is **injected**
   (``subagent_policy``), and a caller that owns a real ``PolicyEngine`` passes one
   backed by an unattended asker: a command the user has already allowlisted runs,
   anything that would need a fresh question is denied with feedback, and the
   unconditional deny floor still applies. Standing permission is not escalation.
4. **A subagent gets its own ``read_files``.** Sharing the parent's would mean a file
   the *subagent* read counts as "seen" for the parent's ``write`` guard — which
   quietly weakens the one rule that prevents most destructive edits.
5. **Failure is a value.** A stalled, budget-exhausted or misconfigured subagent
   returns text the parent can act on. Raising would kill the parent's turn over a
   child's problem.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .core.loop import StalledError, run_turn
from .core.protocols import Policy
from .core.types import (
    AgentState,
    ApprovalDecision,
    Budget,
    Message,
    Role,
    Text,
    TextDelta,
    ToolSpec,
    ToolUse,
    TurnEnd,
)
from .providers.accounting import Ledger
from .providers.bridge import LoopClient
from .providers.router import Role as ModelRole
from .providers.router import Router
from .providers.types import Usage
from .tools.base import ToolContext
from .tools.registry import ToolRegistry
from .tools.task import SubagentRunner, SubagentType

#: A subagent's own ceiling. Generous enough for a real sweep, small enough that a
#: runaway child cannot spend the session. Overridable per type.
DEFAULT_SUBAGENT_BUDGET_TOKENS = 200_000

#: Never handed to a child. See decision 2 in the module docstring.
FORBIDDEN_IN_SUBAGENTS = frozenset({"task"})

#: Builds the policy one nested turn runs under, from that turn's budget. Injected so
#: a caller holding a real ``PolicyEngine`` can let a child act on permission the user
#: already granted, without the child being able to ask for more. See decision 3.
SubagentPolicyFactory = Callable[[Budget], Policy]


@dataclass(frozen=True, slots=True)
class SubagentRun:
    """What one nested turn actually did. Returned to callers, not to the model."""

    kind: str
    summary: str
    stop_reason: str
    turns: int
    spent_tokens: int
    spent_usd: float

    def line(self) -> str:
        return (
            f"{self.kind}: {self.turns} turn(s), {self.spent_tokens:,} tokens, "
            f"${self.spent_usd:.4f}, stopped on {self.stop_reason}"
        )


class SubagentPolicy:
    """The default policy a nested turn runs under: no escalation, its own budget.

    Denies every gated call rather than forwarding to the parent. The built-in
    subagent types are read-only so this never fires for them; a custom type that
    lists a gated tool gets an explicit denial the child can report, instead of a
    prompt appearing in front of a user who asked for something else three steps ago.

    This is the *safe* default, not the right one for every caller: it is what makes
    the builtin ``fixer`` — which declares ``bash`` — unable to run the test it exists
    to fix. A caller that owns a real ``PolicyEngine`` should inject a policy instead
    (:data:`SubagentPolicyFactory`), so a command the user has already allowlisted
    runs while anything needing a fresh question is still refused.
    """

    def __init__(self, budget: Budget) -> None:
        self._budget = budget

    async def approve(
        self, spec: ToolSpec, use: ToolUse, *, rendered: str
    ) -> ApprovalDecision:
        del use, rendered
        return ApprovalDecision(
            approved=False,
            reason=(
                f"{spec.name} needs approval, and a subagent cannot ask the user — "
                "it has no conversation with them. Report what you found and let the "
                "parent agent make the call."
            ),
        )

    def check_budget(self, budget: Budget) -> str | None:
        return "token_budget" if budget.exhausted else None

    def cancelled(self) -> bool:
        # Cancellation reaches a nested turn as CancelledError through the await
        # chain, so there is nothing to poll for here.
        return False


@dataclass(slots=True)
class SubagentSession:
    """Builds and runs subagents for one parent session.

    Keeps the run log so a UI can show "3 subagents, 40k tokens" without the parent
    transcript carrying any of it.
    """

    router: Router
    registry: ToolRegistry
    ledger: Ledger | None = None
    session_id: str = "session"
    budget_tokens: int = DEFAULT_SUBAGENT_BUDGET_TOKENS
    repo_map: str = ""
    policy_for: SubagentPolicyFactory = SubagentPolicy
    runs: list[SubagentRun] = field(default_factory=list)

    def role_for(self, subagent: SubagentType) -> ModelRole:
        """The router role this child runs on. See decision 1.

        An unrecognised role name falls back to ``FAST`` rather than raising: the name
        arrives from a markdown file, and a typo in one subagent definition must not
        take down a session. ``agents.definitions`` validates the value at parse time,
        so a bad one here means the type was built some other way.
        """
        try:
            return ModelRole(subagent.model_role) if subagent.model_role else ModelRole.FAST
        except ValueError:
            return ModelRole.FAST

    def child_context(self) -> ToolContext:
        """A fresh context for a child: same root, its own read set.

        See decision 4. ``vision`` and ``env`` are inherited because they describe
        the machine and the model, not what has been seen.
        """
        parent = self.registry.ctx
        return ToolContext(root=parent.root, vision=parent.vision, env=parent.env)

    def child_registry(self, subagent: SubagentType) -> ToolRegistry:
        """The child's tools: its declared subset, minus anything forbidden."""
        allowed = [name for name in subagent.tools if name not in FORBIDDEN_IN_SUBAGENTS]
        return self.registry.subset(allowed, ctx=self.child_context())

    async def run(self, prompt: str, subagent: SubagentType) -> str:
        """Run one nested turn and return only its final message."""
        try:
            tools = self.child_registry(subagent)
        except ValueError as exc:
            # A type naming a tool this session does not have is a config error, and
            # the model can neither fix nor route around it. Say so plainly.
            return f"the {subagent.name} subagent could not start: {exc}"

        role = self.role_for(subagent)
        spec = self.router.spec_for(role)
        client = LoopClient(
            self.router.for_subagent() if role is ModelRole.FAST else self.router.client_for(role),
            model=spec.model,
            repo_map=self.repo_map,
        )
        budget = Budget(max_tokens=self.budget_tokens)
        state = AgentState(
            messages=(Message(role=Role.USER, content_blocks=(Text(prompt),)),),
            cwd=str(tools.ctx.root),
            budget=budget,
        )

        text_parts: list[str] = []
        stop_reason = "unknown"
        turns = 0
        final_state = state
        try:
            async for event in run_turn(
                state,
                client,
                tools,
                self.policy_for(budget),
                system=subagent.system_prompt,
                max_iterations=subagent.max_iterations,
            ):
                if isinstance(event, TextDelta):
                    text_parts.append(event.text)
                elif isinstance(event, TurnEnd):
                    stop_reason = event.stop_reason or "unknown"
                    turns = event.turn_index + 1
                    if event.agent_state is not None:
                        final_state = event.agent_state
        except StalledError as exc:
            # The child repeated itself. That is a real answer — "I could not make
            # progress" — and far more useful than the parent's turn dying.
            stop_reason = "stalled"
            if exc.agent_state is not None:
                final_state = exc.agent_state
            text_parts.append(
                "\n\n[the subagent stopped: it repeated the same action without "
                "making progress]"
            )
        except asyncio.CancelledError:
            # The user interrupted. Propagate: a cancelled parent must not wait on a
            # child, and swallowing it would leave the nested turn running.
            raise

        summary = "".join(text_parts).strip()
        run = SubagentRun(
            kind=subagent.name,
            summary=summary,
            stop_reason=stop_reason,
            turns=turns,
            spent_tokens=final_state.budget.spent_tokens,
            spent_usd=final_state.budget.spent_usd,
        )
        self.runs.append(run)
        self._record(run, client, role)

        if not summary:
            return (
                f"the {subagent.name} subagent finished without answering "
                f"(stopped on {stop_reason} after {turns} turn(s))"
            )
        if stop_reason in {"max_iterations", "token_budget", "stalled"}:
            # The parent must know the answer is partial, or it will treat a
            # truncated sweep as a complete one.
            return f"{summary}\n\n[partial: the subagent stopped on {stop_reason}]"
        return summary

    def _record(self, run: SubagentRun, client: LoopClient, role: ModelRole) -> None:
        """Bill the child to the role it actually ran on, so the split is honest.

        Billing every child to ``fast`` was fine while every child *was* fast. Once a
        definition can request ``main``, a hardcoded role would hide the one thing the
        per-role split exists to make visible: expensive work routed to an expensive
        model.
        """
        if self.ledger is None:
            return
        request = client.last_request
        fingerprint = str(request.metadata.get("prefix_fingerprint", "")) if request else ""
        self.ledger.record(
            session_id=self.session_id,
            request_id=f"subagent-{len(self.runs)}-{run.kind}",
            role=role,
            spec=self.router.spec_for(role),
            usage=Usage(input_tokens=run.spent_tokens, cost_usd=run.spent_usd),
            reported=run.spent_tokens > 0,
            prefix_fingerprint=fingerprint,
        )

    def summary(self) -> str:
        """One line for the UI: what the children cost, in total."""
        if not self.runs:
            return "no subagents ran"
        tokens = sum(run.spent_tokens for run in self.runs)
        cost = sum(run.spent_usd for run in self.runs)
        kinds = ", ".join(sorted({run.kind for run in self.runs}))
        return (
            f"{len(self.runs)} subagent run(s) [{kinds}] · {tokens:,} tokens · "
            f"${cost:.4f} — none of it in the parent's context"
        )


def build_subagent_runner(
    router: Router,
    registry: ToolRegistry,
    *,
    ledger: Ledger | None = None,
    session_id: str = "session",
    budget_tokens: int = DEFAULT_SUBAGENT_BUDGET_TOKENS,
    repo_map: str = "",
    policy_for: SubagentPolicyFactory = SubagentPolicy,
) -> tuple[SubagentRunner, SubagentSession]:
    """The runner to hand ``build_registry``, plus the session that tracks it.

    Returns both because the runner is a bare callable by design — ``tools.task``
    must not know about sessions — while the caller wants the run log.

    Note the ordering problem this solves: ``TaskTool`` needs a runner, the runner
    needs a registry to subset, and the registry contains ``TaskTool``. Building the
    session against a registry that is filled in afterwards would be a cycle; instead
    the caller builds the registry *without* ``task``, then adds it. See
    :func:`build_session` for the assembled form.
    """
    session = SubagentSession(
        router=router,
        registry=registry,
        ledger=ledger,
        session_id=session_id,
        budget_tokens=budget_tokens,
        repo_map=repo_map,
        policy_for=policy_for,
    )
    return session.run, session


@dataclass(frozen=True, slots=True)
class Session:
    """A fully wired session: tools that can spawn subagents, and a main client."""

    registry: ToolRegistry
    subagents: SubagentSession
    main: LoopClient
    router: Router

    def state(self, prompt: str, *, budget: Budget | None = None) -> AgentState:
        """A fresh :class:`AgentState` for one user request."""
        return AgentState(
            messages=(Message(role=Role.USER, content_blocks=(Text(prompt),)),),
            cwd=str(self.registry.ctx.root),
            budget=budget or Budget(),
        )

    def record_turn(self, final: AgentState, *, request_id: str) -> None:
        """Bill one *parent* turn to the MAIN role.

        Without this the ledger only ever sees subagents, and the per-role split —
        the thing that makes a routing mistake visible — shows one row. Found by the
        demo: it printed a `fast` line and nothing for `main`, on a session where the
        main model had plainly just run.

        Takes the turn's final ``AgentState`` because that is where the loop
        accumulates spend (``_fold_usage`` folds each response into the budget).
        """
        ledger = self.subagents.ledger
        if ledger is None:
            return
        request = self.main.last_request
        fingerprint = str(request.metadata.get("prefix_fingerprint", "")) if request else ""
        ledger.record(
            session_id=self.subagents.session_id,
            request_id=request_id,
            role=ModelRole.MAIN,
            spec=self.router.spec_for(ModelRole.MAIN),
            usage=Usage(
                input_tokens=final.budget.spent_tokens,
                cost_usd=final.budget.spent_usd,
            ),
            reported=final.budget.spent_tokens > 0,
            prefix_fingerprint=fingerprint,
        )


def build_session(
    router: Router,
    ctx: ToolContext,
    *,
    base_tools: ToolRegistry,
    ledger: Ledger | None = None,
    session_id: str = "session",
    repo_map: str = "",
    max_tokens: int = 4096,
    subagent_types: dict[str, SubagentType] | None = None,
    subagent_policy: SubagentPolicyFactory = SubagentPolicy,
) -> Session:
    """Assemble a session from a router, a tool context and a base registry.

    ``base_tools`` must be built *without* ``task`` — pass a registry from
    ``tools.registry.build_registry`` with no ``subagent_runner``. This function adds
    ``task`` on top, wired to a runner that subsets ``base_tools`` for children. That
    ordering is the one non-obvious part of the assembly, and doing it the other way
    round is a cycle.
    """
    from .tools.task import TaskTool

    runner, subagents = build_subagent_runner(
        router,
        base_tools,
        ledger=ledger,
        session_id=session_id,
        repo_map=repo_map,
        policy_for=subagent_policy,
    )
    task_tool = TaskTool(runner, types=subagent_types)
    full = ToolRegistry([*base_tools.tools(), task_tool], ctx)

    main_spec = router.spec_for(ModelRole.MAIN)
    main = LoopClient(
        router.client_for(ModelRole.MAIN),
        model=main_spec.model,
        repo_map=repo_map,
        max_tokens=max_tokens,
    )
    return Session(registry=full, subagents=subagents, main=main, router=router)


def default_context(root: Path | str = ".", *, vision: bool = False) -> ToolContext:
    """A tool context rooted at ``root``. Convenience for callers and the demo."""
    return ToolContext(root=Path(root).resolve(), vision=vision)


__all__ = [
    "DEFAULT_SUBAGENT_BUDGET_TOKENS",
    "FORBIDDEN_IN_SUBAGENTS",
    "Session",
    "SubagentPolicy",
    "SubagentPolicyFactory",
    "SubagentRun",
    "SubagentSession",
    "build_session",
    "build_subagent_runner",
    "default_context",
]
