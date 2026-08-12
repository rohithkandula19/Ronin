"""The programmatic surface: ``Agent``. Ten lines should be enough to be useful.

Import it from the package root — ``from ronin import Agent`` — which is where the
documentation points and what :mod:`ronin` re-exports lazily::

    import asyncio
    from ronin import Agent, AgentConfig

    async def main() -> None:
        # Await a run for the folded result...
        async with await Agent.open(".") as agent:
            result = await agent.run("what does src/ronin/core/loop.py do?")
            print(result.text, result.exit_code)

        # ...or iterate the same run for typed events. One implementation, two shapes.
        agent = Agent(AgentConfig(path=".", tools=[MyTool()]))
        async for event in agent.run("fix the failing test"):
            print(type(event).__name__)
        await agent.aclose()

    asyncio.run(main())

``examples/sdk_quickstart.py`` is this, runnable, with a scripted provider so it needs
no model and no network.

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
from collections.abc import AsyncIterator, Generator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any

from ..context.compaction import Summarizer
from ..core.types import AgentState, Budget, Event, Mode
from ..providers.router import Router, load_config
from ..safety.policy import Asker
from ..tools.base import Tool
from ..ui.headless import ApprovalTracker, exit_code_for
from ..ui.reduce import ViewState, reduce_event
from .spine import Loaded, Note, Paths, Runtime
from .stream import DEFAULT_MAX_ITERATIONS, Conversation, plan_runtime
from .wire import build_runtime, load_workspace

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


def find_router_config(paths: Paths, *, environ: Mapping[str, str] | None = None) -> Path | None:
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


@dataclass(frozen=True, slots=True)
class AgentConfig:
    """Everything :meth:`Agent.open` takes, as one value.

    Exists so ``Agent(config)`` works — a synchronously-constructible agent that opens
    its workspace on first use. Assembling a runtime means reading files, resolving a
    router and possibly connecting to MCP servers, none of which can happen in
    ``__init__`` without an event loop, so the work is deferred rather than pretended
    away: ``Agent(config)`` is cheap and the first ``run`` is where the cost lands.

    Every field means exactly what the same-named argument to :meth:`Agent.open` means.
    """

    path: str | Path = "."
    router: Router | None = None
    mode: Mode | None = None
    home: Path | None = None
    environ: Mapping[str, str] | None = None
    session_id: str | None = None
    record: bool = True
    connect_mcp: bool = True
    asker: Asker | None = None
    #: Tools to add to the ones this workspace builds. Gated like every builtin — see
    #: :func:`ronin.cli.wire.build_runtime`, which folds them in below the gate.
    tools: Sequence[Tool] = ()


class Run:
    """One prompt in flight. Await it for the result, or iterate it for the events.

    Both, from one implementation, because both are the same run: ``await`` folds the
    stream that ``async for`` yields. The alternative — two methods with two bodies —
    is how a fix to one silently misses the other::

        result = await agent.run("fix the failing test")     # AgentResult
        async for event in agent.run("fix the failing test"): # typed events
            ...

    Not reusable: a :class:`Run` is one prompt, and awaiting it twice would run the
    prompt twice against a conversation that has already moved on. Call ``run`` again.
    """

    def __init__(
        self,
        agent: Agent,
        prompt: str,
        *,
        budget: Budget | None = None,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        summarizer: Summarizer | None = None,
        verify: bool = True,
    ) -> None:
        self._agent = agent
        self._prompt = prompt
        self._budget = budget
        self._max_iterations = max_iterations
        self._summarizer = summarizer
        self._verify = verify

    def __aiter__(self) -> AsyncIterator[Event]:
        return self._events()

    def __await__(self) -> Generator[Any, None, AgentResult]:
        return self._fold().__await__()

    async def _events(self) -> AsyncIterator[Event]:
        runtime = await self._agent._opened()
        async for event in self._agent.conversation.run_prompt(
            runtime,
            self._prompt,
            budget=self._budget,
            max_iterations=self._max_iterations,
            summarizer=self._summarizer,
            verify=self._verify,
        ):
            yield event

    async def _fold(self) -> AgentResult:
        """Every event, folded into one result. The ``await`` half of this class."""
        events: list[Event] = []
        # Requests are counted through the tracker, never directly: `core.loop` emits
        # ApprovalRequest before the policy answers, so a raw count reports a granted
        # call as denied and exits 2 on a clean run.
        tracker = ApprovalTracker()
        state = ViewState()
        conversation = self._agent.conversation
        before = len(conversation.notes)
        async for event in self:
            events.append(event)
            state = reduce_event(state, event)
            tracker.observe(event)
        return AgentResult(
            text=state.text,
            exit_code=exit_code_for(state, tracker.resolve()),
            state=state,
            events=tuple(events),
            notes=tuple(conversation.notes[before:]),
        )


class Agent:
    """One workspace, one conversation, one set of live objects.

    Mutable because a conversation is: :attr:`conversation` accumulates messages
    across :meth:`run` calls, and that accumulation is the point (see the module
    docstring). Everything else it holds is frozen.

    Two ways in, and they are the same object either way. ``await Agent.open(path)``
    assembles the runtime immediately and hands back a ready agent; ``Agent(config)``
    is synchronous and assembles on the first run. The second exists because
    ``Agent(config).run(prompt)`` is the shape callers expect from an SDK, and it would
    otherwise need an ``await`` in a constructor.
    """

    def __init__(
        self, target: Runtime | AgentConfig, *, conversation: Conversation | None = None
    ) -> None:
        self._runtime: Runtime | None = target if isinstance(target, Runtime) else None
        self._config: AgentConfig | None = None if isinstance(target, Runtime) else target
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
        asker: Asker | None = None,
        tools: Sequence[Tool] = (),
    ) -> Agent:
        """Load the workspace at ``path`` and assemble a runtime for it.

        ``mode=Mode.PLAN`` is honoured at the *registry* level (see
        :func:`ronin.cli.stream.plan_runtime`): the agent is handed tools that cannot
        edit, rather than a prompt asking it not to. ``resume`` seeds the conversation
        from a state replayed by ``ronin.persistence.resume``.

        ``asker`` is who gets asked when policy says a call needs a human. Omitting it
        means :class:`~ronin.safety.policy.UnattendedAsker` — every gated call refused,
        which is the only safe default for a caller that never said who is watching.
        Passing one is how an interactive front end (the TUI's
        :class:`~ronin.cli.approve.Handoff`) or an embedder with its own consent flow
        answers instead. It is a constructor argument rather than something settable
        later because the policy engine is built here: an agent's approval authority
        cannot change under a turn that is already running.
        """
        paths = Paths.discover(path, home=home)
        flags: dict[str, object] = {} if mode is None else {"mode": mode.value}
        loaded = load_workspace(paths, flags=flags, environ=environ)
        resolved = router if router is not None else load_router(paths, environ=environ)
        runtime = await build_runtime(
            loaded,
            resolved,
            session_id=session_id,
            record=record,
            connect_mcp=connect_mcp,
            asker=asker,
            extra_tools=tools,
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
        """The assembled objects. Raises if this agent has not opened yet.

        An agent built from an :class:`AgentConfig` has no runtime until its first run,
        and assembling one needs an event loop that a property does not have. Raising a
        named error is better than a lazily-synthesized half-runtime, or than ``None``
        threading through every caller that has one already.
        """
        if self._runtime is None:
            raise RuntimeError(
                "this agent was built from an AgentConfig and has not opened yet. "
                "`await agent.ready()` first, or use `await Agent.open(path)`."
            )
        return self._runtime

    @property
    def conversation(self) -> Conversation:
        return self._conversation

    @property
    def loaded(self) -> Loaded:
        return self.runtime.loaded

    async def ready(self) -> Agent:
        """Open the workspace if it is not open, and return self.

        The explicit form of what the first ``run`` does implicitly. Useful when a caller
        wants :attr:`runtime` or :attr:`loaded` before running anything — inspecting the
        tool list, reading the notes — and for a test that wants the assembly to fail
        where it can see it.
        """
        await self._opened()
        return self

    async def _opened(self) -> Runtime:
        """This agent's runtime, assembling it from the config on first use."""
        self._check_open()
        if self._runtime is not None:
            return self._runtime
        config = self._config
        assert config is not None, "an agent has either a runtime or a config"
        opened = await Agent.open(
            config.path,
            router=config.router,
            mode=config.mode,
            home=config.home,
            environ=config.environ,
            session_id=config.session_id,
            record=config.record,
            connect_mcp=config.connect_mcp,
            asker=config.asker,
            tools=config.tools,
            conversation=self._conversation,
        )
        self._runtime = opened._runtime
        return self.runtime

    @property
    def notes(self) -> tuple[Note, ...]:
        """Everything that did not load, plus everything a turn degraded on.

        The workspace's notes are :class:`~ronin.cli.spine.Note` values; the
        conversation's are strings, so they are lifted into notes here rather than
        handing the caller two shapes to switch on.
        """
        return (
            *self.runtime.loaded.notes,
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

    def enter_plan_mode(self) -> None:
        """Narrow this session to plan mode — what ``/plan`` calls. Not reversible.

        The same :func:`~ronin.cli.stream.plan_runtime` the ``--mode plan`` flag goes
        through, so mid-session plan mode and startup plan mode are the same thing: the
        model is handed a registry with no tool that can mutate, rather than a sentence
        asking it not to. Flipping only ``PolicyEngine.mode`` would leave the edit tools on
        the table and let the model spend turns being refused, which is the failure
        ``docs/ARCHITECTURE.md`` cites against the prompt-only version.

        One-way on purpose. Leaving plan mode would mean rebuilding the full registry, and
        a session that can hand itself back the write tools is one keystroke away from
        undoing the guarantee it was put into plan mode for. Restart to leave.
        """
        self._runtime = plan_runtime(self.runtime)
        self.runtime.policy.set_mode(Mode.PLAN)

    def use_model(self, name: str) -> str:
        """Point the *next* turn at a different configured model — what ``/model`` calls.

        Returns the model id now in use. Raises :exc:`KeyError` for a name the config does
        not define; :attr:`ronin.providers.router.Router.model_names` is the vocabulary.

        Only the main model moves. Subagents and compaction resolve their own roles through
        the router, and a switch here deliberately leaves those alone: asking for a stronger
        model to get through one hard turn should not quietly make every summary and every
        nested agent more expensive as well. The conversation keeps its transcript, so this
        is a change of who answers next, not a new session.
        """
        router = self.runtime.session.router
        client = router.client_for_name(name)
        spec = router.config.models[name]
        self._conversation.model = self.runtime.session.main.with_model(client, model=spec.model)
        return spec.model

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

        The same thing as iterating :meth:`run` — this is the spelling that says so at the
        call site, and it is one line over the same :class:`Run` rather than a second
        implementation.

        Not ``async def``: an async generator function's return type already *is*
        ``AsyncIterator``, and declaring both would force callers to write
        ``await agent.stream(...)`` before iterating.
        """
        return self.run(
            prompt,
            budget=budget,
            max_iterations=max_iterations,
            summarizer=summarizer,
            verify=verify,
        ).__aiter__()

    def run(
        self,
        prompt: str,
        *,
        budget: Budget | None = None,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        summarizer: Summarizer | None = None,
        verify: bool = True,
    ) -> Run:
        """One prompt, as something you can await *or* iterate.

        ``await agent.run(p)`` folds the stream into an :class:`AgentResult`, exactly as
        it always has. ``async for event in agent.run(p)`` yields the typed events. Not
        ``async def``, because an awaited coroutine cannot also be iterated — see
        :class:`Run`.

        A closed agent raises *here*, at the call, rather than later at the first
        iteration. Deferring it would move the traceback off the line that has the bug.
        """
        self._check_open()
        return Run(
            self,
            prompt,
            budget=budget,
            max_iterations=max_iterations,
            summarizer=summarizer,
            verify=verify,
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
        # `None` when an AgentConfig-built agent is closed before it ever ran: there is
        # nothing to close, and that is not an error.
        if self._runtime is not None:
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


__all__ = [
    "ROUTER_CONFIG_ENV",
    "ROUTER_CONFIG_NAMES",
    "Agent",
    "AgentResult",
    "find_router_config",
    "load_router",
]
