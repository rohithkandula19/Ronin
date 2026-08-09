"""The programmatic surface: ``Agent``. Ten lines should be enough to be useful.

::

    import asyncio
    from ronin.cli.sdk import Agent

    async def main() -> None:
        async with await Agent.open(".") as agent:
            result = await agent.run("what does src/ronin/core/loop.py do?")
            print(result.text, result.exit_code)

    asyncio.run(main())

Deliberately small, because every method here is a compatibility promise: open on a
directory, run a prompt, or stream the events. Everything else is reachable through
:attr:`Agent.runtime` (the assembled :class:`~ronin.cli.spine.Runtime`) and
:attr:`Agent.conversation` (the accumulated messages), both of which are documented
types owned elsewhere — exposing them is cheaper than mirroring their surface here
and letting the mirror rot.

Two decisions worth stating:

**Async-first, with no synchronous wrapper.** A blocking ``run()`` would have to own
an event loop, and owning one inside a library is how a caller who already has a
loop gets ``RuntimeError: this event loop is already running``. A caller who wants
sync writes ``asyncio.run``, which is one line and theirs.

**One Agent is one conversation.** :meth:`Agent.run` continues the conversation, it
does not restart it, so a second call sees the first call's transcript — which is
what makes compaction, the 200-turn recall property, and ``--continue`` mean
anything. :meth:`Agent.reset` is the explicit way to start over.
"""
from __future__ import annotations

import os
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

from ..context.compaction import Summarizer
from ..core.types import AgentState, Budget, Event, Mode
from ..providers.router import Router, load_config
from ..ui.headless import exit_code_for
from ..ui.reduce import ViewState, reduce_event
from .spine import Loaded, Note, Paths, Runtime
from .stream import DEFAULT_MAX_ITERATIONS, Conversation, plan_runtime

#: Where a router config is looked for, in order, relative to the two roots. A
#: *list*, not a single path, because a workspace pinning its own models is the
#: normal case and a user default is the fallback.
ROUTER_CONFIG_NAMES: tuple[str, ...] = (".ronin/models.toml", "models.toml")

#: Environment variable naming a config file explicitly. Read through an injected
#: mapping, never ``os.environ`` directly, so a test can describe a machine.
ROUTER_CONFIG_ENV = "RONIN_MODELS"

_NO_ROUTER = (
    "no model configuration found. Ronin does not ship a default provider, model id "
    "or price — inventing one would put a wrong number in your ledger. Copy "
    "examples/models.toml to {project} (or {user}), or set {env} to a config path, "
    "or pass router= if you are building one yourself. See docs/PROVIDERS.md."
)


def find_router_config(
    paths: Paths, *, environ: Mapping[str, str] | None = None
) -> Path | None:
    """The router config this workspace should use, or ``None``.

    Searched rather than assumed, and every candidate is reported by
    :func:`load_router` when none of them exists, because "no models configured" is
    the single most common first-run failure and a bare traceback does not fix it.
    """
    env = os.environ if environ is None else environ
    explicit = env.get(ROUTER_CONFIG_ENV, "")
    if explicit:
        candidate = Path(explicit)
        return candidate if candidate.is_file() else None
    for root in (paths.workspace_root, paths.home):
        for name in ROUTER_CONFIG_NAMES:
            candidate = root / name
            if candidate.is_file():
                return candidate
    return None


def load_router(
    paths: Paths,
    *,
    environ: Mapping[str, str] | None = None,
    transport: object | None = None,
) -> Router:
    """Build a :class:`~ronin.providers.router.Router` for this workspace.

    Raises ``FileNotFoundError`` naming every path that was tried. That is on
    purpose: the alternative is a built-in default config, which would have to state
    a model id and a per-million-token price, and a made-up price is a wrong number
    in someone's cost report.
    """
    found = find_router_config(paths, environ=environ)
    if found is None:
        raise FileNotFoundError(
            _NO_ROUTER.format(
                project=paths.workspace_root / ROUTER_CONFIG_NAMES[0],
                user=paths.home / ROUTER_CONFIG_NAMES[0],
                env=ROUTER_CONFIG_ENV,
            )
        )
    config = load_config(found)
    # ``transport`` is the provider layer's HTTP seam; typed as ``object`` here
    # because ``ronin.providers.base.Transport`` is a Protocol this module has no
    # reason to import for one pass-through argument.
    return Router(config, transport=transport, env=environ)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class AgentResult:
    """One prompt's outcome: the answer, the exit code, and the whole stream.

    ``exit_code`` is computed by ``ui.headless.exit_code_for``, so a script that
    checks it agrees exactly with what ``ronin -p`` would have exited — 0 done,
    1 error, 2 an approval was requested and denied. Duplicating that arithmetic
    here is how the two would drift.
    """

    text: str
    exit_code: int
    state: ViewState
    events: tuple[Event, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    @property
    def stop_reason(self) -> str:
        return self.state.stop_reason


class Agent:
    """One workspace, one conversation, one set of live objects.

    Mutable because a conversation is: :attr:`conversation` accumulates messages
    across :meth:`run` calls, and that accumulation is the point (see the module
    docstring). Everything else it holds is frozen.
    """

    def __init__(self, runtime: Runtime, *, conversation: Conversation | None = None) -> None:
        self._runtime = runtime
        self._conversation = Conversation() if conversation is None else conversation
        self._closed = False

    # ------------------------------------------------------------------ opening

    @classmethod
    async def open(
        cls,
        path: str | Path = ".",
        *,
        router: Router | None = None,
        mode: Mode | None = None,
        home: Path | None = None,
        environ: Mapping[str, str] | None = None,
        session_id: str | None = None,
        record: bool = True,
        connect_mcp: bool = True,
        resume: AgentState | None = None,
        conversation: Conversation | None = None,
    ) -> Agent:
        """Load the workspace at ``path`` and assemble a runtime for it.

        ``mode=Mode.PLAN`` is honoured at the *registry* level (see
        :func:`ronin.cli.stream.plan_runtime`): the agent is handed tools that cannot
        edit, rather than a prompt asking it not to. ``resume`` seeds the conversation
        from a state replayed by ``ronin.persistence.resume``.

        ``wire`` is imported here rather than at module scope so importing this module
        stays cheap and so the failure of a missing workspace loader names itself.
        """
        from . import wire

        paths = Paths.discover(path, home=home)
        flags: dict[str, object] = {} if mode is None else {"mode": mode.value}
        loaded = wire.load_workspace(paths, flags=flags, environ=environ)
        resolved = router if router is not None else load_router(paths, environ=environ)
        runtime = await wire.build_runtime(
            loaded,
            resolved,
            session_id=session_id,
            record=record,
            connect_mcp=connect_mcp,
        )
        if loaded.mode is Mode.PLAN:
            runtime = plan_runtime(runtime)
        agent = cls(runtime, conversation=conversation)
        if resume is not None:
            agent._conversation.resume_from(resume)
        return agent

    # --------------------------------------------------------------- inspection

    @property
    def runtime(self) -> Runtime:
        return self._runtime

    @property
    def conversation(self) -> Conversation:
        return self._conversation

    @property
    def loaded(self) -> Loaded:
        return self._runtime.loaded

    @property
    def notes(self) -> tuple[Note, ...]:
        """Everything that did not load, plus everything a turn degraded on.

        The workspace's notes are :class:`~ronin.cli.spine.Note` values; the
        conversation's are strings, so they are lifted into notes here rather than
        handing the caller two shapes to switch on.
        """
        return (
            *self._runtime.loaded.notes,
            *(Note(subject="session", detail=note) for note in self._conversation.notes),
        )

    @property
    def state(self) -> AgentState:
        """The conversation as a resumable value."""
        return self._conversation.state

    def reset(self) -> None:
        """Forget the conversation, keep the runtime. The explicit "start over"."""
        self._conversation = Conversation(
            model=self._conversation.model,
            command=self._conversation.command,
            clock=self._conversation.clock,
        )

    # ------------------------------------------------------------------ running

    def stream(
        self,
        prompt: str,
        *,
        budget: Budget | None = None,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        summarizer: Summarizer | None = None,
        verify: bool = True,
    ) -> AsyncIterator[Event]:
        """The event stream for one prompt, continuing this agent's conversation.

        Not ``async def``: an async generator function's return type already *is*
        ``AsyncIterator``, and declaring both would force callers to write
        ``await agent.stream(...)`` before iterating.
        """
        self._check_open()
        return self._conversation.run_prompt(
            self._runtime,
            prompt,
            budget=budget,
            max_iterations=max_iterations,
            summarizer=summarizer,
            verify=verify,
        )

    async def run(
        self,
        prompt: str,
        *,
        budget: Budget | None = None,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        summarizer: Summarizer | None = None,
        verify: bool = True,
    ) -> AgentResult:
        """Run one prompt to completion and fold the stream into a result."""
        events: list[Event] = []
        approvals: list[ApprovalRequest] = []
        state = ViewState()
        before = len(self._conversation.notes)
        async for event in self.stream(
            prompt,
            budget=budget,
            max_iterations=max_iterations,
            summarizer=summarizer,
            verify=verify,
        ):
            events.append(event)
            state = reduce_event(state, event)
            if isinstance(event, ApprovalRequest):
                approvals.append(event)
        return AgentResult(
            text=state.text,
            exit_code=exit_code_for(state, approvals),
            state=state,
            events=tuple(events),
            notes=tuple(self._conversation.notes[before:]),
        )

    # ------------------------------------------------------------------ closing

    async def aclose(self) -> None:
        """Release everything the runtime opened. Idempotent.

        Delegates to :meth:`ronin.cli.spine.Runtime.aclose`, which runs every closer
        even when one raises and closes the transcript last — the transcript is the
        one piece of state a crash makes unrecoverable.
        """
        if self._closed:
            return
        self._closed = True
        await self._runtime.aclose()

    async def __aenter__(self) -> Agent:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    def _check_open(self) -> None:
        if self._closed:
            raise RuntimeError(
                "this Agent is closed: its shells, MCP servers and transcript have "
                "been released. Open a new one."
            )


def _approval_requests(events: Sequence[Event]) -> tuple[Event, ...]:
    """The approval requests in a stream, for the exit-code arithmetic."""
    from ..core.types import ApprovalRequest

    return tuple(event for event in events if isinstance(event, ApprovalRequest))


__all__ = [
    "ROUTER_CONFIG_ENV",
    "ROUTER_CONFIG_NAMES",
    "Agent",
    "AgentResult",
    "find_router_config",
    "load_router",
]
