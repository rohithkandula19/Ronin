"""Offline doubles for the CLI stream layer. Model, tools, git and subprocesses.

Every seam ``ronin.cli.stream`` touches is faked here and nowhere else, so these
tests run with no network, no provider, no API key and no ``git`` binary:

* :class:`ScriptedModel` satisfies ``core.protocols.ModelClient`` and records every
  call, because most of what the middleware has to prove is about *what reached the
  model* — that the verify failure arrived as a tool result, that the turn-3 tool
  result survived two hundred turns of compaction.
* :class:`ScriptedTools` satisfies ``core.protocols.ToolRegistry`` with declared
  danger levels, which is how "a read-only turn takes no checkpoint" is testable
  without a filesystem.
* :class:`FakeGit` is a :data:`~ronin.verify.runner.CommandRunner` that answers the
  handful of ``git`` invocations ``CheckpointStore`` makes. It is a fake rather than
  a real repo because a real one makes every checkpoint test a subprocess test, and
  the *real* git path already has its own coverage in ``tests/verify``.
* :func:`build_runtime` assembles a real :class:`~ronin.cli.spine.Runtime` —
  real ``PolicyEngine``, real ``CheckpointStore``, real ``Transcript``, real
  ``CompactionPolicy``. Only the model and the subprocesses are doubles.

The names here are deliberately not ``harness``/``fakes``/``conftest``: the test
trees are not packages, so every module under ``tests/`` is importable by its bare
basename and two directories cannot share one.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ronin.agents.hooks import HookConfig, HookRunner
from ronin.cli.spine import Loaded, Paths, Runtime
from ronin.context.compaction import CompactionPolicy
from ronin.context.filestate import FileStateTracker
from ronin.context.memory import Memory, MemoryFile, MemoryScope
from ronin.context.repomap import RepoMap
from ronin.core.protocols import FinalMessage, ModelChunk, TextChunk
from ronin.core.types import (
    ApprovalRequest,
    DangerLevel,
    Message,
    Mode,
    Role,
    Text,
    ToolResult,
    ToolSpec,
    ToolUse,
)
from ronin.persistence.transcript import Transcript
from ronin.providers.base import ModelClient as ProviderClient
from ronin.providers.bridge import LoopClient
from ronin.providers.router import ModelSpec, Router, RouterConfig
from ronin.providers.router import Role as ModelRole
from ronin.providers.types import (
    CONSERVATIVE,
    Capabilities,
    Completed,
    ModelDelta,
    ModelRequest,
    Usage,
)
from ronin.providers.types import TextDelta as ProviderText
from ronin.safety.injection import TaintTracker
from ronin.safety.policy import (
    Answer,
    Asker,
    Decision,
    Outcome,
    PolicyEngine,
    RuleSet,
    UnattendedAsker,
)
from ronin.safety.settings import load_settings
from ronin.session import SubagentSession
from ronin.tools.base import ToolContext
from ronin.tools.registry import ToolRegistry as RealToolRegistry
from ronin.verify.runner import CommandFailure, CommandOutcome
from ronin.verify.spec import DetectedCommand, VerifySpec

# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #

#: A model that can call tools natively. The format shim is the provider layer's own
#: business and has its own coverage; here it would only obscure what is being tested.
NATIVE_TOOLS = Capabilities(
    native_tools=True,
    parallel_tools=True,
    prompt_cache=True,
    thinking=False,
    max_context=200_000,
    vision=False,
)


@dataclass(frozen=True, slots=True)
class Recorded:
    """One recorded ``stream()`` call.

    ``messages`` is snapshotted because ``run_turn`` hands ``stream`` its own live
    list; keeping the list itself would show later mutations and every recorded call
    would look identical.
    """

    system: str
    messages: tuple[Message, ...]
    tools: tuple[ToolSpec, ...]

    @property
    def roles(self) -> tuple[Role, ...]:
        return tuple(message.role for message in self.messages)

    def rendered(self) -> str:
        """Every block's text, concatenated. What the model "saw", for assertions."""
        parts: list[str] = []
        for message in self.messages:
            for block in message.content_blocks:
                parts.append(getattr(block, "text", "") or getattr(block, "content", ""))
                parts.append(str(getattr(block, "arguments", "")))
        return "\n".join(parts)


def say(text: str, *, tokens: int = 8, cost: float = 0.0) -> tuple[ModelChunk, ...]:
    """One response: prose, no tool calls. Ends the turn with ``no_tool_calls``."""
    return (
        TextChunk(text),
        FinalMessage(
            message=Message(role=Role.ASSISTANT, content_blocks=(Text(text),)),
            input_tokens=tokens,
            output_tokens=tokens,
            cost_usd=cost,
        ),
    )


def call(
    name: str, arguments: Mapping[str, Any], *, use_id: str = "c1", text: str = ""
) -> tuple[ModelChunk, ...]:
    """One response: a single tool call, optionally with prose alongside it."""
    blocks: list[Any] = []
    if text:
        blocks.append(Text(text))
    blocks.append(ToolUse(id=use_id, name=name, arguments=dict(arguments)))
    return (
        FinalMessage(
            message=Message(role=Role.ASSISTANT, content_blocks=tuple(blocks)),
            input_tokens=8,
            output_tokens=8,
        ),
    )


class ScriptedModel:
    """A provider that replays a script and records what it was handed.

    Raises when the script runs out rather than yielding an empty stream: an empty
    stream is indistinguishable from the loop's "no final message" protocol error,
    and a mis-scripted test would then pass for the wrong reason.
    """

    def __init__(self, responses: Sequence[Sequence[ModelChunk]]) -> None:
        self._responses = [tuple(chunks) for chunks in responses]
        self.calls: list[Recorded] = []

    @property
    def turns(self) -> int:
        return len(self.calls)

    def stream(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec],
    ) -> AsyncIterator[ModelChunk]:
        self.calls.append(
            Recorded(system=system, messages=tuple(messages), tools=tuple(tools))
        )
        index = len(self.calls) - 1
        if index >= len(self._responses):
            raise AssertionError(
                f"the scripted model was called {len(self.calls)} times but only "
                f"{len(self._responses)} response(s) were scripted"
            )
        return _replay(self._responses[index])


class CyclingModel:
    """A model that answers every turn from one rule. For the 200-turn session.

    Scripting two hundred responses by hand would make the recall test about the
    script rather than about compaction, so the responses are computed from the turn
    number by an injected function.
    """

    def __init__(self, respond: Callable[[int], Sequence[ModelChunk]]) -> None:
        self._respond = respond
        self.calls: list[Recorded] = []

    def stream(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec],
    ) -> AsyncIterator[ModelChunk]:
        self.calls.append(
            Recorded(system=system, messages=tuple(messages), tools=tuple(tools))
        )
        return _replay(tuple(self._respond(len(self.calls) - 1)))


async def _replay(chunks: Sequence[ModelChunk]) -> AsyncIterator[ModelChunk]:
    for chunk in chunks:
        await asyncio.sleep(0)  # a scheduling yield, so cancellation lands somewhere
        yield chunk


class HangingModel:
    """A model that never finishes its first stream. For the cancellation test."""

    def __init__(self) -> None:
        self.started = asyncio.Event()

    def stream(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec],
    ) -> AsyncIterator[ModelChunk]:
        del system, messages, tools
        return self._stream()

    async def _stream(self) -> AsyncIterator[ModelChunk]:
        self.started.set()
        yield TextChunk("thinking")
        await asyncio.Event().wait()  # never set; only a cancel gets out of here


class StubProviderClient:
    """A ``providers.base.ModelClient`` for a :class:`Router` that must not dial out.

    Used where the code under test reaches ``runtime.session.main`` or
    ``router.for_compaction()`` — the router builds a real ``LoopClient`` chain and
    this is the thing at the bottom of it.
    """

    def __init__(self, text: str = "stub", capabilities: Capabilities = CONSERVATIVE) -> None:
        self._text = text
        self._capabilities = capabilities
        self.requests: list[ModelRequest] = []

    def capabilities(self) -> Capabilities:
        return self._capabilities

    def stream(self, req: ModelRequest) -> AsyncIterator[ModelDelta]:
        self.requests.append(req)
        return self._stream()

    async def _stream(self) -> AsyncIterator[ModelDelta]:
        yield ProviderText(self._text)
        yield Completed(
            message=Message(role=Role.ASSISTANT, content_blocks=(Text(self._text),)),
            usage=Usage(input_tokens=4, output_tokens=4),
        )


class ScriptedProviderClient:
    """A ``providers.base.ModelClient`` with a script, for the end-to-end tests.

    The *provider* seam rather than the loop seam: everything above it — the router,
    the retry wrapper, ``LoopClient``, the stable-prefix assembly — is the real code,
    which is the point of an integration test.
    """

    def __init__(
        self,
        responses: Sequence[Sequence[ModelDelta]],
        *,
        capabilities: Capabilities = NATIVE_TOOLS,
    ) -> None:
        self._responses = [tuple(deltas) for deltas in responses]
        self._capabilities = capabilities
        self.requests: list[ModelRequest] = []

    def capabilities(self) -> Capabilities:
        return self._capabilities

    def stream(self, req: ModelRequest) -> AsyncIterator[ModelDelta]:
        self.requests.append(req)
        index = len(self.requests) - 1
        if index >= len(self._responses):
            raise AssertionError(
                f"the scripted provider was called {len(self.requests)} times but "
                f"only {len(self._responses)} response(s) were scripted"
            )
        return _replay_deltas(self._responses[index])


async def _replay_deltas(deltas: Sequence[ModelDelta]) -> AsyncIterator[ModelDelta]:
    for delta in deltas:
        await asyncio.sleep(0)
        yield delta


def provider_says(text: str) -> tuple[ModelDelta, ...]:
    return (
        ProviderText(text),
        Completed(
            message=Message(role=Role.ASSISTANT, content_blocks=(Text(text),)),
            usage=Usage(input_tokens=10, output_tokens=6),
        ),
    )


def provider_calls(
    name: str, arguments: Mapping[str, Any], *, use_id: str = "p1"
) -> tuple[ModelDelta, ...]:
    return (
        Completed(
            message=Message(
                role=Role.ASSISTANT,
                content_blocks=(ToolUse(id=use_id, name=name, arguments=dict(arguments)),),
            ),
            usage=Usage(input_tokens=10, output_tokens=6),
        ),
    )


def scripted_router(
    responses: Sequence[Sequence[ModelDelta]],
) -> tuple[Router, ScriptedProviderClient]:
    """A real :class:`Router` whose one model is a scripted provider client."""
    spec = ModelSpec(name="scripted", provider="stub", model="scripted-1")
    config = RouterConfig(roles={ModelRole.MAIN: "scripted"}, models={"scripted": spec})
    client = ScriptedProviderClient(responses)

    def build(_spec: ModelSpec, **_kwargs: object) -> ProviderClient:
        return client

    return Router(config, build=build), client


@dataclass(frozen=True, slots=True)
class ApprovingAsker:
    """A human who says yes. The one place a test grants approval, named as such."""

    async def ask(self, request: ApprovalRequest) -> Answer:
        del request
        return Answer(outcome=Outcome.YES_ONCE, feedback="approved by the test")


def stub_router(*, text: str = "stub") -> tuple[Router, StubProviderClient]:
    """A router whose every role resolves to one offline stub client."""
    spec = ModelSpec(name="stub", provider="stub", model="stub-1")
    config = RouterConfig(roles={ModelRole.MAIN: "stub"}, models={"stub": spec})
    client = StubProviderClient(text=text)

    def build(_spec: ModelSpec, **_kwargs: object) -> ProviderClient:
        return client

    return Router(config, build=build), client


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class FakeTool:
    """A declared tool with a scripted answer. ``danger`` is what the gate reads."""

    name: str
    danger: DangerLevel = DangerLevel.READ_ONLY
    requires_approval: bool = False
    result: ToolResult = field(default_factory=lambda: ToolResult(ok=True, content="ok"))
    #: Called instead of returning ``result`` when set — for a tool that must write
    #: a real file so a real diff exists.
    handler: Callable[[Mapping[str, Any]], ToolResult] | None = None

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description=f"{self.name} (test double)",
            danger_level=self.danger,
            requires_approval=self.requires_approval,
        )


class ScriptedTools:
    """A ``core.protocols.ToolRegistry`` over :class:`FakeTool` values."""

    def __init__(self, tools: Sequence[FakeTool]) -> None:
        self._tools = {tool.name: tool for tool in tools}
        self.executed: list[ToolUse] = []

    def specs(self) -> Sequence[ToolSpec]:
        return tuple(tool.spec() for tool in self._tools.values())

    def get(self, name: str) -> ToolSpec | None:
        tool = self._tools.get(name)
        return tool.spec() if tool is not None else None

    async def execute(self, use: ToolUse) -> ToolResult:
        self.executed.append(use)
        tool = self._tools.get(use.name)
        if tool is None:
            return ToolResult(ok=False, error=f"no tool called {use.name!r}")
        if tool.handler is not None:
            return tool.handler(use.arguments)
        return tool.result


def writer(root: Path, *, name: str = "edit") -> FakeTool:
    """A mutating tool that really writes a file, so a real diff exists afterwards."""

    def handle(arguments: Mapping[str, Any]) -> ToolResult:
        target = root / str(arguments.get("file_path", "unnamed.txt"))
        target.parent.mkdir(parents=True, exist_ok=True)
        body = str(arguments.get("content", "x"))
        target.write_text(body, encoding="utf-8", newline="\n")
        return ToolResult(
            ok=True, content=f"wrote {target.name}", artifacts=(str(target),)
        )

    return FakeTool(name=name, danger=DangerLevel.MUTATING, handler=handle)


def reader(*, name: str = "read", content: str = "file contents") -> FakeTool:
    return FakeTool(name=name, result=ToolResult(ok=True, content=content))


# --------------------------------------------------------------------------- #
# git and subprocesses
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class FakeGit:
    """Answers the ``git`` calls ``CheckpointStore`` makes, and records them.

    Mutable because it counts commits: each ``rev-parse HEAD`` must return a *new*
    id or the store's session base and every checkpoint would collide, and the tests
    that assert "a second checkpoint was taken" would pass on one.
    """

    diff: str = ""
    commits: int = 0
    argv_log: list[tuple[str, ...]] = field(default_factory=list)
    fail: str = ""
    #: Called the moment a checkpoint commit runs. This is how "the checkpoint was
    #: taken *before* the edit" is asserted on the filesystem rather than on the
    #: order of two events, which proves less.
    on_commit: Callable[[], None] | None = None

    async def __call__(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout: float = 0.0,
        env: Mapping[str, str] | None = None,
    ) -> CommandOutcome:
        del cwd, timeout, env
        args = tuple(argv)
        self.argv_log.append(args)
        joined = " ".join(args)
        if self.fail and self.fail in joined:
            return CommandOutcome(argv=args, exit_code=1, output=f"fake git refused {self.fail}")
        if "commit" in args:
            self.commits += 1
            if self.on_commit is not None:
                self.on_commit()
            return CommandOutcome(argv=args, exit_code=0)
        if "rev-parse" in args:
            return CommandOutcome(argv=args, exit_code=0, output=f"{self.commits:040x}")
        if "diff" in args:
            return CommandOutcome(argv=args, exit_code=0, output=self.diff)
        if "log" in args:
            return CommandOutcome(argv=args, exit_code=0, output="")
        return CommandOutcome(argv=args, exit_code=0)

    @property
    def checkpoints_taken(self) -> int:
        return sum(1 for args in self.argv_log if "commit" in args)


def missing_git() -> Callable[..., Any]:
    """A runner that reports ``git`` is not installed, for the degraded path."""

    async def run(
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout: float = 0.0,
        env: Mapping[str, str] | None = None,
    ) -> CommandOutcome:
        del cwd, timeout, env
        return CommandOutcome(
            argv=tuple(argv),
            exit_code=127,
            output="git: not found",
            failure=CommandFailure.NOT_FOUND,
        )

    return run


@dataclass(slots=True)
class ScriptedCommands:
    """A verify-command runner: one queued output per call, then the last repeats.

    The last outcome repeating is what makes "the same failure signature three
    times" expressible without scripting an exact number of runs the repair loop's
    internals would then be able to change.
    """

    outputs: list[tuple[int, str]]
    argv_log: list[tuple[str, ...]] = field(default_factory=list)
    index: int = 0

    async def __call__(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout: float = 0.0,
        env: Mapping[str, str] | None = None,
    ) -> CommandOutcome:
        del cwd, timeout, env
        self.argv_log.append(tuple(argv))
        position = min(self.index, len(self.outputs) - 1)
        self.index += 1
        code, output = self.outputs[position]
        return CommandOutcome(argv=tuple(argv), exit_code=code, output=output)

    @property
    def runs(self) -> int:
        return len(self.argv_log)


#: A pytest failure, in the shape the repair loop's signature normalizer expects.
PYTEST_FAILURE = """\
============================= test session starts ==============================
collected 2 items

tests/test_widget.py .F                                                  [100%]

=================================== FAILURES ===================================
_____________________________ test_widget_rounds ______________________________
tests/test_widget.py:14: in test_widget_rounds
    assert widget(1.005) == 1.01
E   AssertionError: assert 1.0 == 1.01
=========================== short test summary info ============================
FAILED tests/test_widget.py::test_widget_rounds - AssertionError: assert 1.0 == 1.01
========================= 1 failed, 1 passed in 0.11s ==========================
"""

#: The same failure one run later: different tmpdir, moved line, different timing.
#: The repair loop must read these two as one signature.
PYTEST_FAILURE_AGAIN = PYTEST_FAILURE.replace("test_widget.py:14", "test_widget.py:19").replace(
    "in 0.11s", "in 0.08s"
)

DIFF_ONE_FILE = """\
diff --git a/src/widget.py b/src/widget.py
index 1111111..2222222 100644
--- a/src/widget.py
+++ b/src/widget.py
@@ -1,2 +1,2 @@
-def widget(x): return round(x, 2)
+def widget(x): return round(x + 0.0001, 2)
"""


# --------------------------------------------------------------------------- #
# Loaded and Runtime
# --------------------------------------------------------------------------- #


def build_loaded(
    root: Path,
    *,
    home: Path | None = None,
    mode: Mode = Mode.AUTO_EDIT,
    memory_text: str = "",
    repo_map_tokens: int = 0,
    verify: VerifySpec | None = None,
) -> Loaded:
    """A real :class:`~ronin.cli.spine.Loaded` for a workspace in ``root``.

    ``repo_map_tokens`` and ``memory_text`` exist to drive
    ``Loaded.pinned_prefix_tokens()``: the compaction test proves the pinned prefix
    is *counted* by making it the only reason the trigger is reached.
    """
    home_dir = (home or root / "home").resolve()
    home_dir.mkdir(parents=True, exist_ok=True)
    paths = Paths(workspace_root=root.resolve(), home=home_dir, cwd=root.resolve())
    settings = load_settings(home=home_dir, cwd=root.resolve(), flags={"mode": mode.value})
    memory = (
        Memory(
            files=(
                MemoryFile(
                    path="RONIN.md", scope=MemoryScope.PROJECT, text=memory_text, depth=0
                ),
            ),
            root=str(root),
        )
        if memory_text
        else Memory()
    )
    return Loaded(
        paths=paths,
        settings=settings,
        memory=memory,
        repo_map=RepoMap(token_estimate=repo_map_tokens),
        verify=verify if verify is not None else VerifySpec(),
    )


def pytest_spec() -> VerifySpec:
    """A verify spec whose one command is a ``pytest`` run on the changed tests."""
    return VerifySpec(
        test=DetectedCommand(
            argv=("pytest", "-q"), source="stream_harness", evidence="the test harness"
        )
    )


def build_runtime(
    loaded: Loaded,
    *,
    tools: ScriptedTools | None = None,
    git: Callable[..., Any] | None = None,
    transcript: Transcript | None = None,
    context_window: int = 4_000,
    default_decision: Decision = Decision.ALLOW,
    asker: Asker | None = None,
    system: str = "you are ronin (test)",
) -> Runtime:
    """A real :class:`~ronin.cli.spine.Runtime` with fakes only at the model, tool
    and subprocess seams.

    The policy engine, checkpoint store, transcript, compaction policy, taint
    tracker and hook runner are the shipped objects: the point of these tests is the
    joins, and a faked join proves nothing.
    """
    from ronin.verify.checkpoints import CheckpointStore

    root = loaded.paths.workspace_root
    router, _stub = stub_router()
    context = ToolContext(root=root)
    registry = RealToolRegistry([], context)
    session_registry = registry
    main = LoopClient(router.client_for(ModelRole.MAIN), model="stub-1")
    from ronin.session import Session

    session = Session(
        registry=session_registry,
        subagents=SubagentSession(router=router, registry=session_registry),
        main=main,
        router=router,
    )
    engine = PolicyEngine(
        rules=RuleSet(rules=(), default=default_decision),
        mode=loaded.mode,
        asker=asker if asker is not None else UnattendedAsker(),
    )
    store = CheckpointStore(root, run=git) if git is not None else CheckpointStore(root)
    return Runtime(
        loaded=loaded,
        session=session,
        registry=tools if tools is not None else ScriptedTools(()),
        policy=engine,
        files=FileStateTracker(),
        taint=TaintTracker(),
        hooks=HookRunner(config=HookConfig()),
        checkpoints=store,
        compaction=CompactionPolicy(context_window=context_window),
        system=system,
        transcript=transcript,
    )


def git_repo(root: Path) -> Path:
    """Make ``root`` look like a git repo to ``CheckpointStore``'s probe.

    The probe checks for ``.git`` on disk before shelling out, so a directory and a
    fake runner together cover the whole store without a real repository.
    """
    (root / ".git").mkdir(parents=True, exist_ok=True)
    return root


async def collect(stream: AsyncIterator[Any]) -> list[Any]:
    """Drain an async iterator into a list."""
    return [item async for item in stream]


__all__ = [
    "DIFF_ONE_FILE",
    "NATIVE_TOOLS",
    "PYTEST_FAILURE",
    "PYTEST_FAILURE_AGAIN",
    "ApprovingAsker",
    "CyclingModel",
    "FakeGit",
    "FakeTool",
    "HangingModel",
    "Recorded",
    "ScriptedCommands",
    "ScriptedModel",
    "ScriptedProviderClient",
    "ScriptedTools",
    "StubProviderClient",
    "build_loaded",
    "build_runtime",
    "call",
    "collect",
    "git_repo",
    "missing_git",
    "provider_calls",
    "provider_says",
    "pytest_spec",
    "reader",
    "say",
    "scripted_router",
    "stub_router",
    "writer",
]
