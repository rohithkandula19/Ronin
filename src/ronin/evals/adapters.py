"""The one narrow protocol the runner drives an agent through, and two implementations.

::

    RunAgent = Callable[[EvalTask, Path], Awaitable[AgentOutcome]]

One prompt, one workspace, one record of what happened. Everything else the runner
needs — the baseline, the verification, the wall clock — is the runner's, not the
adapter's, which is why this returns :class:`~ronin.evals.taxonomy.AgentOutcome` and
not the full ``RunRecord``: an adapter that also had to run ``verify.sh`` would be a
second copy of the runner, and the v1 shell-out below would be unwritable.

**The v2 adapter never constructs a provider client.** It takes an
:data:`AgentFactory`, so a test drives it with a scripted fake and a real run passes
:func:`sdk_agent_factory`. The agent is reached through the :class:`AgentLike`
protocol rather than by importing ``ronin.cli.sdk``: ``Agent`` satisfies it
structurally, and depending on the shape instead of the class is what keeps the eval
package testable without assembling a whole ``Runtime``.

**The v1 adapter is a comparison convenience and nothing more.** It shells out to the
legacy ``packages/cli`` ``ronin code`` command, which needs real credentials and
therefore cannot run in this suite at all. It exists so "is v2 actually better than
what we had" is answerable by a person with keys, and it is best-effort in a specific
way: the legacy CLI does not emit a machine-readable trace, so its ``AgentOutcome``
has no tool calls, no turn count and no usage — the edited paths come from hashing
the workspace before and after. Do not read a v1 taxonomy breakdown as comparable to
a v2 one; only the pass/fail column is.
"""
from __future__ import annotations

import hashlib
import shlex
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..core.loop import fingerprint
from ..core.types import (
    AgentState,
    Budget,
    Error,
    Event,
    ToolEnd,
    ToolStart,
    ToolUse,
    TurnEnd,
    TurnStart,
)
from ..verify.runner import CommandOutcome, CommandRunner, run_command
from .task import EvalTask
from .taxonomy import EDIT_TOOLS, AgentOutcome, ToolCallRecord, UsageRecord

#: The stop reason ``ronin.cli.stream`` reports when ``core.loop`` raised ``StalledError``.
STALLED_STOP_REASON = "stalled"

#: The legacy CLI's coding subcommand. Named as a constant because it is the whole
#: contract of the v1 adapter and it lives in another package's argv.
V1_ARGV_HEAD: tuple[str, ...] = ("ronin", "code")


# --------------------------------------------------------------------------- #
# The protocol the runner drives
# --------------------------------------------------------------------------- #

#: One task, one workspace, one record. The runner knows nothing else about agents.
RunAgent = Callable[[EvalTask, Path], Awaitable[AgentOutcome]]


class AgentRun(Protocol):
    """The shape of one prompt's result. ``ronin.cli.sdk.AgentResult`` satisfies it."""

    @property
    def text(self) -> str: ...

    @property
    def exit_code(self) -> int: ...

    @property
    def events(self) -> tuple[Event, ...]: ...

    @property
    def notes(self) -> tuple[str, ...]: ...


class AgentLike(Protocol):
    """The shape of an agent. ``ronin.cli.sdk.Agent`` satisfies it structurally.

    Structural rather than nominal so this package never imports the application
    layer for a type: a test supplies a thirty-line fake, and nothing has to build a
    ``Runtime`` to exercise the adapter.
    """

    @property
    def state(self) -> AgentState: ...

    async def run(self, prompt: str, *, max_iterations: int = ...) -> AgentRun: ...

    async def aclose(self) -> None: ...


@dataclass(frozen=True, slots=True)
class OpenedAgent:
    """An agent ready to run, plus the tool names it was given.

    The registry is carried alongside rather than reached through the agent because
    ``HALLUCINATED_API`` needs "was this tool name registered", and asking the agent
    for its registry would drag the whole ``Runtime`` type into this module's
    signatures for one tuple of strings.
    """

    agent: AgentLike
    registered_tools: tuple[str, ...] = ()


#: Opens an agent rooted at a workspace for one task. Injected, always: the eval
#: package must never decide which model runs.
AgentFactory = Callable[[EvalTask, Path], Awaitable[OpenedAgent]]


# --------------------------------------------------------------------------- #
# Folding an event stream into evidence
# --------------------------------------------------------------------------- #


def relative_to(path: str, root: Path) -> str:
    """``path`` as a workspace-relative posix string, or unchanged if it is outside.

    Left unchanged rather than dropped: a path outside the workspace is exactly the
    evidence ``WRONG_FILE`` is looking for, and silently discarding it would turn the
    most interesting failure into "the agent edited nothing".
    """
    candidate = Path(path)
    try:
        return candidate.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return candidate.as_posix()


def outcome_from_events(
    events: Sequence[Event],
    *,
    workspace: Path,
    final_text: str = "",
    registered_tools: Sequence[str] = (),
    budget: Budget | None = None,
    usage: UsageRecord | None = None,
    error: str = "",
) -> AgentOutcome:
    """Fold a loop event stream into the evidence the taxonomy consumes.

    Pure over the stream, so a test writes the events it wants classified instead of
    driving a whole agent to produce them. Every field the taxonomy needs is derived
    here and nowhere else, which is what makes "the runner records X" checkable.
    """
    calls: dict[str, ToolStart] = {}
    records: list[ToolCallRecord] = []
    edited: list[str] = []
    turns = 0
    stalled = False
    stop_reason = ""
    notes: list[str] = []

    for event in events:
        if isinstance(event, TurnStart):
            turns += 1
        elif isinstance(event, ToolStart):
            calls[event.tool_use_id] = event
        elif isinstance(event, ToolEnd):
            start = calls.get(event.tool_use_id)
            arguments: Mapping[str, object] = start.arguments if start else {}
            mark = fingerprint(
                ToolUse(id=event.tool_use_id, name=event.name, arguments=arguments)
            )
            paths = tuple(relative_to(item, workspace) for item in event.result.artifacts)
            records.append(
                ToolCallRecord(
                    name=event.name,
                    fingerprint=mark,
                    ok=event.result.ok,
                    error=event.result.error,
                    content=event.result.content,
                    paths=paths,
                )
            )
            if event.result.ok and event.name in EDIT_TOOLS:
                edited.extend(paths)
        elif isinstance(event, TurnEnd):
            stop_reason = event.stop_reason or stop_reason
            if event.stop_reason == STALLED_STOP_REASON:
                stalled = True
        elif isinstance(event, Error):
            notes.append(f"{event.kind}: {event.message}")
            if event.kind == STALLED_STOP_REASON:
                stalled = True

    resolved_usage = usage
    if resolved_usage is None:
        resolved_usage = (
            usage_from_budget(budget, requests=turns) if budget is not None else UsageRecord()
        )

    return AgentOutcome(
        turns_used=turns,
        tool_calls=tuple(records),
        edited_paths=tuple(dict.fromkeys(edited)),
        final_text=final_text,
        stalled=stalled,
        stop_reason=stop_reason,
        registered_tools=tuple(registered_tools),
        usage=resolved_usage,
        error=error,
        notes=tuple(notes),
    )


def usage_from_budget(budget: Budget, *, requests: int = 0) -> UsageRecord:
    """Tokens and cost from ``AgentState.budget``, with unreported kept distinct.

    ``Budget`` carries a total and no split, and it is folded from whatever the
    provider said. A provider that reported nothing leaves ``spent_tokens`` at zero
    after real requests, which is **unreported, not zero** — the distinction
    ``providers.accounting`` makes on its rows, made here so a report cannot average
    a silent provider in as if it were free.
    """
    reported = budget.spent_tokens > 0
    return UsageRecord(
        reported=reported,
        # A budget cannot tell "unpriced model" from "free request"; spend is the only
        # evidence available, so priced means "money was actually attributed".
        priced=budget.spent_usd > 0,
        tokens=budget.spent_tokens,
        cost_usd=budget.spent_usd,
        requests=requests,
        unreported_requests=0 if reported else requests,
    )


def usage_from_totals(
    *,
    requests: int,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    unpriced_requests: int,
    unreported_requests: int,
) -> UsageRecord:
    """Build a :class:`UsageRecord` from ``providers.accounting.Totals``' fields.

    Takes the fields rather than the object so this module does not import the
    provider layer for one adapter; :func:`ledger_usage` is the one caller that reads
    them off a real ``Totals``.
    """
    return UsageRecord(
        reported=requests > unreported_requests,
        priced=unpriced_requests == 0,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
        requests=requests,
        unreported_requests=unreported_requests,
    )


def ledger_usage(ledger: object, session_id: str) -> UsageRecord:
    """Read one session's totals out of a ``providers.accounting.Ledger``.

    ``ledger`` is typed as ``object`` and duck-called: this is the only place the eval
    package touches the ledger, and taking the concrete type would put a provider
    import in a module the runner imports on every path, including the ones with no
    ledger at all. A ledger that does not answer ``session_totals`` yields an
    unreported record rather than raising, because a broken accounting hookup must
    not fail an eval run — it must show up as "usage unreported", which is exactly
    what it is.
    """
    totals = getattr(ledger, "session_totals", None)
    if totals is None:
        return UsageRecord()
    resolved = totals(session_id)
    return usage_from_totals(
        requests=int(resolved.requests),
        input_tokens=int(resolved.input_tokens),
        output_tokens=int(resolved.output_tokens),
        cost_usd=float(resolved.cost_usd),
        unpriced_requests=int(resolved.unpriced_requests),
        unreported_requests=int(resolved.unreported_requests),
    )


# --------------------------------------------------------------------------- #
# v2 — the primary adapter
# --------------------------------------------------------------------------- #


def v2_adapter(
    open_agent: AgentFactory,
    *,
    usage_for: Callable[[EvalTask, OpenedAgent], UsageRecord] | None = None,
) -> RunAgent:
    """A :data:`RunAgent` over an injected agent factory.

    ``usage_for`` overrides the default (tokens from ``AgentState.budget``) with, for
    example, :func:`ledger_usage` against a real ledger. The agent is always closed,
    including when the prompt raises: an unclosed agent leaves shells and MCP servers
    behind, and sixty of those is a hung suite.
    """

    async def run(task: EvalTask, workspace: Path) -> AgentOutcome:
        opened = await open_agent(task, workspace)
        try:
            result = await opened.agent.run(task.prompt, max_iterations=task.max_turns)
        except Exception as exc:
            # An adapter failure is a *result*, not an exception: one task that cannot
            # open a model must not end the run over the other fifty-nine.
            return AgentOutcome(error=f"{type(exc).__name__}: {exc}")
        finally:
            await opened.agent.aclose()

        usage = (
            usage_for(task, opened)
            if usage_for is not None
            else usage_from_budget(
                opened.agent.state.budget, requests=_turn_count(result.events)
            )
        )
        return outcome_from_events(
            result.events,
            workspace=workspace,
            final_text=result.text,
            registered_tools=opened.registered_tools,
            usage=usage,
        )

    return run


def _turn_count(events: Sequence[Event]) -> int:
    return sum(1 for event in events if isinstance(event, TurnStart))


async def sdk_agent_factory(
    task: EvalTask,
    workspace: Path,
    *,
    router: object,
    mode: object | None = None,
    connect_mcp: bool = False,
) -> OpenedAgent:
    """Open a real ``ronin.cli.sdk.Agent`` on ``workspace``.

    Imported inside the function so the eval package stays importable — and its whole
    unit suite runnable — without assembling the application layer. ``router`` is
    typed as ``object`` for the same reason and is passed straight through; there is
    exactly one caller and it is the CLI, which has the real type.

    ``record=False`` and ``connect_mcp=False`` by default: a transcript per task times
    sixty tasks is a lot of disk for data nothing reads, and an eval that reaches an
    MCP server is an eval measuring somebody else's uptime.
    """
    from ..cli.sdk import Agent  # noqa: PLC0415 - see the docstring

    agent = await Agent.open(
        workspace,
        router=router,  # type: ignore[arg-type]
        mode=mode,  # type: ignore[arg-type]
        record=False,
        connect_mcp=connect_mcp,
    )
    names = tuple(sorted(spec.name for spec in agent.runtime.registry.specs()))
    return OpenedAgent(agent=agent, registered_tools=names)


# --------------------------------------------------------------------------- #
# v1 — best-effort comparison only
# --------------------------------------------------------------------------- #


def tree_digest(root: Path) -> dict[str, str]:
    """``relative posix path -> sha256`` for every file under ``root``.

    The v1 CLI emits no machine-readable trace, so "which files did it edit" has to
    come from the filesystem. Content hashes rather than mtimes: a tool that rewrites
    a file with identical bytes did not edit it, and mtime says otherwise.
    """
    digests: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_dir() or "__pycache__" in path.parts:
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        digests[path.relative_to(root).as_posix()] = hashlib.sha256(data).hexdigest()
    return digests


def changed_paths(before: Mapping[str, str], after: Mapping[str, str]) -> tuple[str, ...]:
    """Paths added, removed or altered between two :func:`tree_digest` snapshots."""
    names = set(before) | set(after)
    return tuple(sorted(name for name in names if before.get(name) != after.get(name)))


def v1_argv(task: EvalTask, workspace: Path) -> tuple[str, ...]:
    """The legacy CLI invocation for one task. Separated out so a test can read it."""
    return (
        *V1_ARGV_HEAD,
        task.prompt,
        "--root",
        str(workspace),
        "--yolo",
        "--max-steps",
        str(task.max_turns),
    )


def v1_adapter(
    *,
    command_runner: CommandRunner = run_command,
    timeout: float | None = None,
) -> RunAgent:
    """**Best-effort.** A :data:`RunAgent` that shells out to ``packages/cli``'s ``ronin code``.

    Not a supported path. It exists so v2 can be compared against what shipped
    before, and it is honest about what it cannot see: no tool calls, no turn count,
    no usage, so every taxonomy class that needs those is structurally unreachable
    for a v1 row. Only the pass/fail column of a v1 report is comparable to a v2 one.

    The legacy CLI requires real provider credentials, so this can never run inside
    this repository's offline suite; the tests here cover the argv it builds and the
    outcome it folds from a scripted :data:`CommandRunner`.
    """

    async def run(task: EvalTask, workspace: Path) -> AgentOutcome:
        before = tree_digest(workspace)
        argv = v1_argv(task, workspace)
        outcome: CommandOutcome = await command_runner(
            list(argv),
            cwd=workspace,
            **({} if timeout is None else {"timeout": timeout}),
        )
        after = tree_digest(workspace)
        error = "" if outcome.ok else f"v1 cli: {outcome.describe()}"
        return AgentOutcome(
            edited_paths=changed_paths(before, after),
            final_text=outcome.output,
            stop_reason=f"exit {outcome.exit_code}",
            error=error,
            notes=(
                "v1 adapter: best-effort. turns, tool calls and usage are not "
                "observable through the legacy CLI, so taxonomy classes that need "
                "them cannot fire for this row.",
                f"argv: {shlex.join(argv)}",
            ),
        )

    return run


__all__ = [
    "STALLED_STOP_REASON",
    "V1_ARGV_HEAD",
    "AgentFactory",
    "AgentLike",
    "AgentRun",
    "OpenedAgent",
    "RunAgent",
    "changed_paths",
    "ledger_usage",
    "outcome_from_events",
    "relative_to",
    "sdk_agent_factory",
    "tree_digest",
    "usage_from_budget",
    "usage_from_totals",
    "v1_adapter",
    "v1_argv",
    "v2_adapter",
]
